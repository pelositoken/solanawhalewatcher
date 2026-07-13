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

_INTERVAL_DELTA = {
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
    delta = _INTERVAL_DELTA.get(interval)
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


def make_price_provider(name: str) -> PriceProvider:
    if name == "yfinance":
        return YFinanceProvider()
    raise ValueError(f"Unknown price provider: {name!r} (available: yfinance)")
