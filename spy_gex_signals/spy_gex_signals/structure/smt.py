"""SMT (smart-money) divergence — optional confidence booster, NEVER a gate.

At the inducement sweep, compare against a genuinely correlated instrument:
if the primary broke its swing point but the correlated instrument failed to
break its own corresponding swing, the sweep is suspicious (divergence).

Guard required by the spec: divergence between uncorrelated markets is
noise. A rolling return-correlation check runs first; below the threshold
SMT reports 'disabled_low_correlation' instead of an opinion.
"""

from __future__ import annotations

import pandas as pd

from ..config import SmtConfig
from .models import Bar, Direction, SwingKind
from .swings import MarketStructure


def frame_to_bars(df: pd.DataFrame) -> list[Bar]:
    return [
        Bar(index=i, ts=ts.to_pydatetime(), open=float(r["open"]), high=float(r["high"]),
            low=float(r["low"]), close=float(r["close"]), volume=float(r.get("volume", 0.0)))
        for i, (ts, r) in enumerate(df.iterrows())
    ]


class SmtChecker:
    """Streaming divergence checker over a correlated instrument's LTF bars."""

    def __init__(self, cfg: SmtConfig, swing_strength: int,
                 primary_df: pd.DataFrame, correlated_df: pd.DataFrame | None,
                 correlated_symbol: str = ""):
        self.cfg = cfg
        self.correlated_symbol = correlated_symbol
        self._primary_closes = primary_df["close"] if primary_df is not None else None
        self._bars = frame_to_bars(correlated_df) if correlated_df is not None else []
        self._ms = MarketStructure(swing_strength)
        self._cursor = 0
        self._closes = correlated_df["close"] if correlated_df is not None else None

    def _advance(self, until_ts) -> None:
        """Feed correlated bars whose OPEN time is <= until_ts (same-bar alignment;
        both series use the same interval so equal open times are simultaneous)."""
        while self._cursor < len(self._bars) and self._bars[self._cursor].ts <= until_ts:
            self._ms.update(self._bars[self._cursor])
            self._cursor += 1

    def _correlation_at(self, ts) -> float | None:
        if self._closes is None or self._primary_closes is None:
            return None
        joined = pd.concat([self._primary_closes.rename("p"), self._closes.rename("c")],
                           axis=1, join="inner").loc[:ts]
        tail = joined.tail(self.cfg.correlation_lookback_bars)
        if len(tail) < max(20, self.cfg.correlation_lookback_bars // 4):
            return None
        rets = tail.pct_change().dropna()
        if rets.empty:
            return None
        return float(rets["p"].corr(rets["c"]))

    def check(self, sweep_ts, direction: Direction) -> tuple[str, dict]:
        """Returns (status, detail). Status is one of:
        'divergence' | 'no_divergence' | 'disabled_low_correlation' | 'not_available'
        """
        if not self.cfg.enabled or not self._bars:
            return "not_available", {}

        self._advance(sweep_ts)
        corr = self._correlation_at(sweep_ts)
        if corr is None:
            return "not_available", {"reason": "insufficient overlapping history"}
        if corr < self.cfg.min_correlation:
            return "disabled_low_correlation", {"correlation": round(corr, 3),
                                                "min_required": self.cfg.min_correlation}

        # The swing on the correlated instrument that corresponds to the swept
        # inducement: its most recent confirmed swing of the same kind.
        kind = SwingKind.LOW if direction == Direction.LONG else SwingKind.HIGH
        swing = self._ms.last_swing(kind)
        if swing is None:
            return "not_available", {"reason": "no confirmed swing on correlated instrument"}

        # Did the correlated instrument break its swing around the sweep
        # (same bar or the two before it)?
        recent = [b for b in self._bars[:self._cursor]][-3:]
        if not recent:
            return "not_available", {"reason": "no correlated bars at sweep time"}
        if direction == Direction.LONG:
            broke = any(b.low < swing.price for b in recent)
        else:
            broke = any(b.high > swing.price for b in recent)

        detail = {"correlation": round(corr, 3), "correlated_symbol": self.correlated_symbol,
                  "correlated_swing": swing.price, "correlated_broke": broke}
        return ("no_divergence" if broke else "divergence"), detail
