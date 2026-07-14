"""Fractal swing detection and market-structure tracking (streaming).

A swing high at bar j (strength k) = high[j] strictly exceeds the highs of
the k bars on each side. It therefore only becomes KNOWN at bar j+k — the
tracker enforces that lag, which is what keeps the Phase 3 backtest free of
lookahead.

MSU = the active swing sequence (alternating highs/lows after same-kind
dedup, keeping the more extreme). MSS = a body close beyond the last
confirmed swing point. MSS is an annotation and is never an entry trigger
anywhere in this codebase (double-MSU trap guardrail).
"""

from __future__ import annotations

from collections import deque

from .models import Bar, Bias, Swing, SwingKind


class SwingDetector:
    """Streaming fractal detector: feed bars in order, get swings as they confirm."""

    def __init__(self, strength: int):
        if strength < 1:
            raise ValueError("swing strength must be >= 1")
        self.k = strength
        self._window: deque[Bar] = deque(maxlen=2 * strength + 1)

    def update(self, bar: Bar) -> list[Swing]:
        """Returns swings confirmed BY this bar (center of the full window)."""
        self._window.append(bar)
        if len(self._window) < self._window.maxlen:
            return []
        center = self._window[self.k]
        others = [b for i, b in enumerate(self._window) if i != self.k]
        out: list[Swing] = []
        if all(center.high > b.high for b in others):
            out.append(Swing(SwingKind.HIGH, center.high, center.index, center.ts,
                             confirmed_at_index=bar.index))
        if all(center.low < b.low for b in others):
            out.append(Swing(SwingKind.LOW, center.low, center.index, center.ts,
                             confirmed_at_index=bar.index))
        return out


class MarketStructure:
    """MSU tracker: alternating swing sequence, trend read, and MSS detection."""

    def __init__(self, strength: int):
        self.detector = SwingDetector(strength)
        self.swings: list[Swing] = []       # alternating after dedup
        self.mss_events: list[dict] = []    # annotations only — never entries
        self._last_bar: Bar | None = None

    def update(self, bar: Bar) -> list[Swing]:
        """Feed one completed bar; returns newly confirmed swings."""
        confirmed = self.detector.update(bar)
        for s in confirmed:
            self._append(s)
        self._detect_mss(bar)
        self._last_bar = bar
        return confirmed

    def _append(self, swing: Swing) -> None:
        if self.swings and self.swings[-1].kind == swing.kind:
            last = self.swings[-1]
            more_extreme = (swing.price > last.price if swing.kind == SwingKind.HIGH
                            else swing.price < last.price)
            if more_extreme:
                self.swings[-1] = swing
            return
        self.swings.append(swing)

    def _detect_mss(self, bar: Bar) -> None:
        """Body close beyond the most recent confirmed swing point = MSS."""
        h = self.last_swing(SwingKind.HIGH)
        if h is not None and bar.index > h.confirmed_at_index and bar.close > h.price:
            if not any(e["swing_index"] == h.bar_index and e["direction"] == "bullish"
                       for e in self.mss_events):
                self.mss_events.append({"direction": "bullish", "swing_index": h.bar_index,
                                        "swing_price": h.price, "break_index": bar.index,
                                        "break_close": bar.close})
        l = self.last_swing(SwingKind.LOW)
        if l is not None and bar.index > l.confirmed_at_index and bar.close < l.price:
            if not any(e["swing_index"] == l.bar_index and e["direction"] == "bearish"
                       for e in self.mss_events):
                self.mss_events.append({"direction": "bearish", "swing_index": l.bar_index,
                                        "swing_price": l.price, "break_index": bar.index,
                                        "break_close": bar.close})

    def last_swing(self, kind: SwingKind) -> Swing | None:
        for s in reversed(self.swings):
            if s.kind == kind:
                return s
        return None

    def trend(self) -> Bias:
        """HH+HL → bullish, LL+LH → bearish, else neutral. Needs 2 highs + 2 lows."""
        highs = [s for s in self.swings if s.kind == SwingKind.HIGH][-2:]
        lows = [s for s in self.swings if s.kind == SwingKind.LOW][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return Bias.NEUTRAL
        hh = highs[1].price > highs[0].price
        hl = lows[1].price > lows[0].price
        lh = highs[1].price < highs[0].price
        ll = lows[1].price < lows[0].price
        if hh and hl:
            return Bias.BULLISH
        if lh and ll:
            return Bias.BEARISH
        return Bias.NEUTRAL
