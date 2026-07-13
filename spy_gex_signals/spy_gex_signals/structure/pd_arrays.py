"""PD-array detection: FVG, no-wick candles, simple TQL annotation.

Implemented here because the 4-step process needs them (FVG for Entry 2,
no-wick/TQL as MSU annotations). Deliberately deferred: iFVG as entry logic
(tracked as a role-flip annotation only, later phase if wanted) and CME
gaps (specific to futures charts with weekend sessions — revisit when gold
runs on real GC futures data).
"""

from __future__ import annotations

from .models import Bar, Direction, Fvg, Swing


def detect_fvg(b1: Bar, b2: Bar, b3: Bar) -> Fvg | None:
    """Three-candle imbalance: gap between bar1's and bar3's wicks.

    Bullish FVG: b3.low > b1.high (the up-move left unfilled space).
    Bearish FVG: b3.high < b1.low.
    """
    if b3.low > b1.high:
        return Fvg(Direction.LONG, upper=b3.low, lower=b1.high, created_index=b3.index)
    if b3.high < b1.low:
        return Fvg(Direction.SHORT, upper=b3.high, lower=b1.low, created_index=b3.index)
    return None


def is_no_wick(bar: Bar, side: str, tolerance_frac: float = 0.0005) -> bool:
    """No-wick candle on the given side ('top'|'bottom') — one-directional,
    uncontested delivery; marks a clean liquidity boundary.

    tolerance_frac allows a hair of wick relative to the bar's range so real
    data (which rarely prints exact equality) still qualifies.
    """
    rng = bar.high - bar.low
    if rng <= 0:
        return True
    tol = rng * tolerance_frac if tolerance_frac else 0.0
    if side == "top":
        return (bar.high - bar.body_high) <= tol
    if side == "bottom":
        return (bar.body_low - bar.low) <= tol
    raise ValueError("side must be 'top' or 'bottom'")


def trendline_liquidity(swings: list[Swing], tolerance_pct: float = 0.002) -> dict | None:
    """Simple TQL annotation: >=3 successive same-kind swings that sit near a
    straight line (liquidity pooling along a trendline). Annotation only —
    never a gate.

    Returns {'kind', 'swing_indices', 'slope_per_bar'} or None.
    """
    if len(swings) < 3:
        return None
    last3 = swings[-3:]
    if len({s.kind for s in last3}) != 1:
        return None
    (x0, y0), (x1, y1), (x2, y2) = [(s.bar_index, s.price) for s in last3]
    if x2 == x0:
        return None
    slope = (y2 - y0) / (x2 - x0)
    expected_mid = y0 + slope * (x1 - x0)
    if y1 == 0:
        return None
    if abs(y1 - expected_mid) / abs(y1) <= tolerance_pct:
        return {"kind": last3[0].kind.value,
                "swing_indices": [s.bar_index for s in last3],
                "slope_per_bar": slope}
    return None
