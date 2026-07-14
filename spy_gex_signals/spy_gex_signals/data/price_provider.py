"""Price data providers.

All providers return bars as a pandas DataFrame with a tz-aware UTC
DatetimeIndex (bar OPEN time) and lowercase columns
open/high/low/close/volume. The last, still-forming intraday bar is
dropped so downstream logic can never peek at an incomplete candle
(no-lookahead guarantee).

Default implementation is yfinance (free). Swap providers via
config price.provider — a Polygon/Databento/broker provider only has to
implement the same interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)

# yfinance practical history caps per interval
_YF_DEFAULT_PERIOD = {
    "1d": "max",
    "1h": "730d",
    "15m": "60d",
    "5m": "60d",
    "1m": "7d",
}

INTERVAL_DELTA = {
    "1d": timedelta(days=1),
    "1h": timedelta(hours=1),
    "15m": timedelta(minutes=15),
    "5m": timedelta(minutes=5),
    "1m": timedelta(minutes=1),
}


class PriceProvider(ABC):
    name: str

    @abstractmethod
    def get_history(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """Completed bars only, UTC index, columns open/high/low/close/volume."""

    @abstractmethod
    def get_latest_price(self, symbol: str) -> float:
        """Most recent trade/close price (delayed on free sources)."""

    def get_latest_quote(self, symbol: str) -> tuple[float, datetime]:
        """(price, as_of_ts). HARD REQUIREMENT for consumers: any surface that
        renders this price (dashboard, alert, log line a human reads) must
        render the timestamp with it — 'as of HH:MM UTC, ~N min delayed' —
        never a bare number. Data from historical endpoints is delayed and
        that delay must stay visible end to end (Phase 7 alert format)."""
        return self.get_latest_price(symbol), datetime.now(timezone.utc)

    def get_roll_dates(self, symbol: str) -> list:
        """Timestamps where a continuous futures symbol rolled to a new raw
        contract during the last get_history fetch. Roll gaps are splice
        artifacts, not market moves — downstream must exclude signals whose
        window spans one. Empty for equities and non-continuous sources."""
        return []


def _standardize(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    # yfinance can return multiindex columns for single tickers on some versions
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = df[~df.index.duplicated(keep="last")].sort_index()

    # No-lookahead: drop the final bar if it hasn't finished forming yet.
    delta = INTERVAL_DELTA.get(interval)
    if delta is not None and len(df) > 0:
        last_open = df.index[-1].to_pydatetime()
        if last_open + delta > datetime.now(timezone.utc):
            df = df.iloc[:-1]
    return df


class YFinanceProvider(PriceProvider):
    name = "yfinance"

    def get_history(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        import yfinance as yf

        kwargs: dict = {"interval": interval, "auto_adjust": False}
        if start is not None:
            kwargs["start"] = start
            if end is not None:
                kwargs["end"] = end
        else:
            kwargs["period"] = _YF_DEFAULT_PERIOD.get(interval, "60d")

        raw = yf.Ticker(symbol).history(**kwargs)
        df = _standardize(raw, interval)
        log.info("yfinance history %s %s: %d completed bars (%s → %s)",
                 symbol, interval, len(df),
                 df.index[0] if len(df) else "-", df.index[-1] if len(df) else "-")
        return df

    def get_latest_price(self, symbol: str) -> float:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period="1d", interval="1m", auto_adjust=False)
        if df.empty:
            df = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
        if df.empty:
            raise RuntimeError(f"No price data returned for {symbol}")
        return float(df["Close"].iloc[-1])


_RESAMPLE_INTERVALS = {"5m": "5min", "15m": "15min"}

# Native Databento OHLCV schemas per interval; 5m/15m resample from ohlcv-1m.
_DB_SCHEMA = {"1d": "ohlcv-1d", "1h": "ohlcv-1h", "1m": "ohlcv-1m",
              "5m": "ohlcv-1m", "15m": "ohlcv-1m"}
_DB_NATIVE_LOOKBACK_DAYS = {"1d": 3650, "1h": 730}


def resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """1m → 5m/15m, left-labeled left-closed bins ([09:30, 09:35) → 09:30),
    empty bins dropped, so bar timing matches every other provider."""
    rule = _RESAMPLE_INTERVALS[interval]
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"})
    return out.dropna(subset=["open"])


class DatabentoPriceProvider(PriceProvider):
    """Databento historical bars. Key: DATABENTO_API_KEY env var ONLY.

    Continuous futures (stype_in='continuous') are UNADJUSTED splices: the
    provider records roll dates (raw-contract changes) per fetch, exposed via
    get_roll_dates(). Cost guardrail: every fetch prints the metadata
    cost estimate first and the running session total after.
    """

    name = "databento"

    def __init__(self, symbol_mapping, intraday_lookback_days: int = 365):
        import os
        if not os.environ.get("DATABENTO_API_KEY"):
            raise RuntimeError(
                "DATABENTO_API_KEY environment variable is not set. Set it in "
                "your shell (never in code or config.yaml) and retry.")
        import databento  # lazy: only needed when this provider is selected

        self._client = databento.Historical()  # reads DATABENTO_API_KEY itself
        self._mapping = symbol_mapping  # callable: price_symbol -> {dataset, symbol, stype_in?}
        self._intraday_lookback_days = intraday_lookback_days
        self._roll_dates: dict[str, list] = {}
        self._session_cost = 0.0

    def _map(self, symbol: str) -> dict:
        m = self._mapping(symbol) if callable(self._mapping) else self._mapping.get(symbol)
        if not m:
            raise ValueError(
                f"No Databento mapping for {symbol!r}. Add it under the "
                f"instrument's 'databento:' block or price.databento_symbols "
                f"in config.yaml.")
        return m

    def get_history(self, symbol: str, interval: str,
                    start: datetime | None = None,
                    end: datetime | None = None) -> pd.DataFrame:
        if interval not in _DB_SCHEMA:
            raise ValueError(f"Unsupported interval {interval!r} for databento")
        m = self._map(symbol)
        schema = _DB_SCHEMA[interval]
        now = datetime.now(timezone.utc)
        if start is None:
            days = (self._intraday_lookback_days if schema == "ohlcv-1m"
                    else _DB_NATIVE_LOOKBACK_DAYS.get(interval, 365))
            start = now - timedelta(days=days)
        end = end or now

        params = dict(dataset=m["dataset"], symbols=[m["symbol"]], schema=schema,
                      start=start, end=end)
        if m.get("stype_in"):
            params["stype_in"] = m["stype_in"]

        # Cost guardrail: estimate BEFORE fetching, running total after.
        try:
            est = float(self._client.metadata.get_cost(**params))
        except Exception as e:  # estimate failure shouldn't block the fetch
            est = float("nan")
            log.warning("databento cost estimate failed for %s: %s", symbol, e)
        print(f"[databento] estimated ${est:.4f} for {m['dataset']}/{schema}/"
              f"{m['symbol']} {start:%Y-%m-%d}→{end:%Y-%m-%d}")

        store = self._client.timeseries.get_range(**params)
        df = store.to_df(map_symbols=True)

        if est == est:  # not NaN
            self._session_cost += est
        print(f"[databento] session data spend so far: ~${self._session_cost:.4f}")

        if df.empty:
            self._roll_dates[symbol] = []
            return _standardize(df, interval)

        # Roll detection for continuous futures: mapped raw contract changes.
        if m.get("stype_in") == "continuous" and "symbol" in df.columns:
            raw = df["symbol"].astype(str)
            changes = df.index[raw.ne(raw.shift()) & raw.shift().notna()]
            self._roll_dates[symbol] = list(changes)
            if len(changes):
                log.info("databento %s: %d roll(s) in fetched range", symbol, len(changes))
        else:
            self._roll_dates[symbol] = []

        df = df[["open", "high", "low", "close", "volume"]]
        if schema == "ohlcv-1m" and interval in _RESAMPLE_INTERVALS:
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df = resample_ohlcv(df, interval)
        out = _standardize(df, interval)
        log.info("databento history %s %s: %d completed bars", symbol, interval, len(out))
        return out

    def get_latest_price(self, symbol: str) -> float:
        return self.get_latest_quote(symbol)[0]

    def get_latest_quote(self, symbol: str) -> tuple[float, datetime]:
        """Most recent available 1m bar close + its bar-close timestamp.
        Databento historical availability lags real time by a few minutes;
        consumers MUST display the as-of timestamp (see base class docstring)."""
        df = self.get_history(symbol, "1m",
                              start=datetime.now(timezone.utc) - timedelta(days=3))
        if df.empty:
            raise RuntimeError(f"No recent databento bars for {symbol}")
        as_of = df.index[-1].to_pydatetime() + INTERVAL_DELTA["1m"]
        return float(df["close"].iloc[-1]), as_of

    def get_roll_dates(self, symbol: str) -> list:
        return self._roll_dates.get(symbol, [])


def make_price_provider(name: str, config=None) -> PriceProvider:
    if name == "yfinance":
        return YFinanceProvider()
    if name == "databento":
        if config is None:
            raise ValueError("databento provider needs the loaded Config for symbol mappings")
        return DatabentoPriceProvider(
            symbol_mapping=config.databento_mapping,
            intraday_lookback_days=config.price.databento_intraday_lookback_days)
    raise ValueError(f"Unknown price provider: {name!r} (available: yfinance, databento)")
