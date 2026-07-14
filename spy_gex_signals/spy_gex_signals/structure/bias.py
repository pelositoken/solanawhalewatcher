"""HTF bias and DOL (draw on liquidity) selection.

Mechanical rule (confirmed with the user):
- bias = direction of the most recent confirmed HTF swing sequence
  (higher-highs + higher-lows → bullish; lower-lows + lower-highs → bearish;
  otherwise neutral → no setups).
- DOL = the NEAREST UNSWEPT external HTF swing beyond current price in the
  bias direction (swing high above price when bullish, swing low below when
  bearish). External liquidity only — internal levels (IPAs) are waypoints,
  not destinations.

Swept-tracking uses LTF price extremes so a level is retired the moment any
trade runs it, not a full HTF bar later.
"""

from __future__ import annotations

from ..config import StructureConfig
from .models import Bar, Bias, Swing, SwingKind
from .swings import MarketStructure


class HtfContext:
    def __init__(self, cfg: StructureConfig):
        self.cfg = cfg
        self.structure = MarketStructure(cfg.swing_strength)
        self.bias: Bias = Bias.NEUTRAL

    def on_htf_bar(self, bar: Bar) -> list[Swing]:
        confirmed = self.structure.update(bar)
        # HTF price action retires older levels too (matters for HTF history
        # that predates the LTF data window). Strict inequality means a swing
        # can never be swept by its own extreme bar.
        self._mark(bar)
        self.bias = self.structure.trend()
        return confirmed

    def mark_sweeps(self, ltf_bar: Bar) -> list[Swing]:
        """Retire external levels the moment LTF price trades beyond them."""
        return self._mark(ltf_bar)

    def _mark(self, bar: Bar) -> list[Swing]:
        newly_swept = []
        for s in self.structure.swings:
            if s.swept:
                continue
            if s.kind == SwingKind.HIGH and bar.high > s.price:
                s.swept = True
                newly_swept.append(s)
            elif s.kind == SwingKind.LOW and bar.low < s.price:
                s.swept = True
                newly_swept.append(s)
        return newly_swept

    def current_dol(self, current_price: float) -> Swing | None:
        """Nearest unswept external swing beyond price in the bias direction."""
        if self.bias == Bias.BULLISH:
            candidates = [s for s in self.structure.swings
                          if s.kind == SwingKind.HIGH and not s.swept and s.price > current_price]
            return min(candidates, key=lambda s: s.price) if candidates else None
        if self.bias == Bias.BEARISH:
            candidates = [s for s in self.structure.swings
                          if s.kind == SwingKind.LOW and not s.swept and s.price < current_price]
            return max(candidates, key=lambda s: s.price) if candidates else None
        return None

    def check_dol_gex_alignment(self, dol: Swing | None) -> str:
        """Phase 4 hook: does the DOL coincide with a major GEX strike within
        gex.dol_strike_tolerance_pct? Not wired until the GEX gate phase —
        callers must record the returned status, never assume alignment."""
        return "not_wired"
