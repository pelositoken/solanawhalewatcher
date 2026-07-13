"""Hand-crafted synthetic scenarios with known geometry.

Used by the test suite and by `scripts/scan_structure.py --fixture` so the
engine can be demonstrated end-to-end deterministically (this sandbox has no
market-data network access; the user runs the live scan locally).

Base long scenario:
- HTF (1h): swing low 95 → swing high 105 → higher low 98 → higher high 112,
  then a pullback holding ~104-107. Bias: bullish. DOL: the unswept 112 high
  (the 105 high was swept by the 112 leg and must NOT be picked as DOL).
- LTF (5m): 20 warmup bars (monotonic — no swings), then an up-leg that
  confirms an inducement swing low at 103.0, a sweep bar wicking to 102.6
  that closes weak, and a recovery bar closing at 104.2 — above the sweep
  candle's range midpoint (103.65) but NOT above the sweep candle's body
  high (104.6), so exactly one CSD rule ('50pct') fires. Entry 1 fills at
  the next bar's open, 104.2.
- Correlated instrument: the same tape scaled ×10, except its sweep bar low
  HOLDS above its own swing low → SMT divergence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

_START = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)

HTF_BARS = [
    # (open, high, low, close)
    (100.0, 101.0, 99.5, 100.5),
    (100.5, 101.5, 100.0, 101.0),
    (99.0, 99.5, 95.0, 96.0),      # swing low 95.0 (L1)
    (96.0, 98.0, 95.5, 97.5),
    (97.5, 100.0, 97.0, 99.5),     # confirms L1
    (99.5, 105.0, 99.0, 104.0),    # swing high 105.0 (H1)
    (104.0, 104.5, 102.0, 103.0),
    (103.0, 103.5, 101.0, 102.0),  # confirms H1
    (102.0, 102.5, 98.0, 99.0),    # swing low 98.0 (L2, higher low)
    (99.0, 102.0, 98.5, 101.5),
    (101.5, 104.0, 101.0, 103.5),  # confirms L2
    (103.5, 112.0, 103.0, 111.0),  # swing high 112.0 (H2, higher high; sweeps H1)
    (111.0, 111.5, 106.0, 107.0),
    (107.0, 108.0, 105.0, 106.0),  # confirms H2 → bias bullish, DOL = 112
    (106.0, 107.0, 104.5, 105.0),
    (105.0, 106.5, 104.0, 104.5),
    (104.5, 106.0, 104.0, 105.5),
    (105.5, 106.5, 104.5, 106.0),
    (106.0, 107.0, 105.0, 106.5),
    (106.5, 107.5, 105.0, 105.5),
]

# The 14-bar core: swing low → sweep → CSD → entry.
LTF_CORE = [
    (104.0, 104.3, 103.8, 104.2),
    (104.2, 104.5, 104.0, 104.4),
    (104.4, 104.6, 103.9, 104.0),
    (104.0, 104.2, 103.4, 103.6),
    (103.6, 103.8, 103.0, 103.5),  # inducement swing low 103.0
    (103.5, 104.0, 103.2, 103.9),
    (103.9, 104.4, 103.6, 104.3),  # confirms the swing low
    (104.3, 104.6, 104.0, 104.5),
    (104.5, 104.8, 104.2, 104.6),
    (104.6, 104.7, 102.6, 103.0),  # SWEEP bar: wick to 102.6 < 103.0; range mid 103.65
    (103.0, 104.9, 102.9, 104.2),  # CSD bar: close 104.2 > 103.65 (50pct only)
    (104.2, 105.0, 104.1, 104.8),  # entry bar: Entry 1 fills at open 104.2
    (104.8, 105.0, 104.6, 104.9),
    (104.9, 105.1, 104.7, 105.0),
]


def _warmup(n: int, lo: float, step: float) -> list[tuple]:
    """Monotonically rising bars — strictly rising highs AND lows can never
    print a fractal swing, so warmup adds history without adding setups."""
    return [(lo + step * i + 0.1, lo + step * i + 0.3, lo + step * i,
             lo + step * i + 0.2) for i in range(n)]


def _to_frame(bars: list[tuple], start: datetime, interval: timedelta) -> pd.DataFrame:
    idx = pd.DatetimeIndex([start + i * interval for i in range(len(bars))])
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    return df


def htf_frame() -> pd.DataFrame:
    return _to_frame(HTF_BARS, _START, timedelta(hours=1))


def ltf_frame(core: list[tuple] | None = None, drift_bars: int = 20) -> pd.DataFrame:
    core = core if core is not None else LTF_CORE
    warm = _warmup(20, 101.9, 0.1)  # ends at low 103.8, flush with the core
    last_close = core[-1][3]
    drift = _warmup(drift_bars, last_close - 0.2, 0.05)
    # LTF starts after every HTF bar has completed, so HTF context is fully
    # formed (bias bullish, DOL 112) from the first LTF bar.
    start = _START + timedelta(hours=len(HTF_BARS))
    return _to_frame(warm + core + drift, start, timedelta(minutes=5))


def correlated_frame() -> pd.DataFrame:
    """Same tape ×10, but the sweep bar's low holds above its swing low
    (1030.0×... not broken) → SMT divergence at the primary's sweep."""
    df = ltf_frame().copy() * 10.0
    df["volume"] = 1000.0
    # Hold the lows through the sweep AND confirmation bars — the engine
    # evaluates SMT at CSD time over the last few bars, so the correlated
    # instrument must fail to break its 1030.0 swing low across that window.
    df.loc[df.index[20 + 9], "low"] = 1031.0
    df.loc[df.index[20 + 9], "close"] = 1032.0
    df.loc[df.index[20 + 10], "low"] = 1031.0
    return df


def ltf_frame_no_csd() -> pd.DataFrame:
    """Variant: the sweep happens but every subsequent close stays below the
    sweep candle's midpoint (wicks poke above — closes never do) → the setup
    must expire with no signal. Wick-only recovery must never trigger."""
    # Closes decline steadily so neither CSD rule can fire: never above the
    # sweep candle's midpoint (50pct) and never above the prior candle's body
    # high (prior_candle — each close is below the previous bar's close).
    # Lows hold above the 102.6 sweep extreme so the confirmation window
    # isn't restarted; highs wick above the 103.65 midpoint on purpose.
    core = LTF_CORE[:10] + [
        (103.00, 103.90, 102.90, 103.10),
        (103.10, 103.80, 102.85, 103.05),
        (103.05, 103.85, 102.80, 103.00),
        (103.00, 103.80, 102.75, 102.95),
        (102.95, 103.75, 102.70, 102.90),
        (102.90, 103.80, 102.70, 102.85),
        (102.85, 103.70, 102.70, 102.80),
    ]
    return ltf_frame(core=core, drift_bars=5)


def ltf_frame_slow_recovery() -> pd.DataFrame:
    """Variant for the prior-candle-resolves-to-sweep-candle regression:
    confirmation comes 3 bars after the sweep, through small interim bodies.

    Sweep candle: o=104.6 h=104.7 l=102.6 c=103.0 → body high 104.6,
    range mid 103.65 → prior_candle threshold = max(104.6, 103.65) = 104.6.

    - bar +1 closes 103.2 (beats nothing)
    - bar +2 closes 103.4 — ABOVE the interim bar's body high (103.2), which
      the old (literal predecessor) semantics would have confirmed on, but
      far below the sweep candle's 104.6 → must NOT confirm
    - bar +3 closes 104.8 > 104.6 → prior_candle confirms here
    - bar +4 open 104.8 = Entry 1 fill
    """
    core = LTF_CORE[:10] + [
        (103.0, 103.3, 102.8, 103.2),
        (103.2, 103.5, 103.0, 103.4),
        (103.4, 104.9, 103.3, 104.8),
        (104.8, 105.0, 104.6, 104.9),
    ]
    return ltf_frame(core=core, drift_bars=10)


def ltf_frame_fvg_retest(retrace: bool = True) -> pd.DataFrame:
    """Variant exercising Entry 2 (FVG retest).

    CSD comes 3 bars after the sweep via a displacement candle whose 3-bar
    window (bars +1, +2, +3 after the sweep) leaves a bullish FVG:
    b1 high 103.4 < b3 low 103.5 → gap [103.4, 103.5], entry at 103.5.

    retrace=True: two bars later price dips to 103.45 → fill at 103.5.
      Stop 102.6×(1−0.0005)=102.5487, risk 0.9513, 3R target 106.3539.
    retrace=False: price runs away upward → entry expires unfilled.
    """
    post_sweep = [
        (103.0, 103.4, 102.9, 103.3),   # weak: below mid 103.65, below body 104.6
        (103.3, 103.6, 103.2, 103.5),   # still weak; high 103.6
        (103.6, 105.2, 103.5, 105.0),   # displacement CSD bar; low 103.5 > 103.4
    ]
    if retrace:
        tail = [
            (105.0, 105.1, 104.0, 104.2),
            (104.2, 104.3, 103.45, 103.8),  # touches 103.5 → filled
            (103.8, 104.0, 103.6, 103.9),
        ]
        return ltf_frame(core=LTF_CORE[:10] + post_sweep + tail, drift_bars=10)
    tail = [(105.0, 105.3, 104.8, 105.2)]
    return ltf_frame(core=LTF_CORE[:10] + post_sweep + tail, drift_bars=25)


# ---------------------------------------------------------------------------
# Short-side scenario — values derived independently by hand, NOT mirrored
# from the long fixture, per user review point 4.
#
# HTF: H1 135 → L1 124 → H2 130 (lower high) → L2 118 (lower low; the L2 leg
# sweeps L1) → pullback holding 118..130. Bias bearish. DOL must be 118
# (the swept 124 low must be skipped).
#
# LTF: downtrend, inducement swing HIGH 125.2 (confirmed 2 bars later),
# sweep bar wicks to 125.6 (o=123.2 h=125.6 l=123.1 c=125.0 → range mid
# 124.35, body LOW 123.2), next close 123.9 < 124.35 → CSD '50pct' only
# (123.9 is NOT below the clamped prior threshold min(123.2, 124.35)=123.2).
# Entry 1 fills next open 123.9. Expected arithmetic:
#   stop   = 125.6 × 1.0005          = 125.6628
#   risk   = 125.6628 − 123.9        =   1.7628
#   target = 123.9 − 3 × 1.7628      = 118.6116
#   DOL R  = (123.9 − 118) / 1.7628  ≈   3.3470
# ---------------------------------------------------------------------------

HTF_BARS_BEAR = [
    (130.0, 131.0, 129.0, 130.5),
    (130.5, 132.0, 129.5, 131.0),
    (131.0, 135.0, 130.5, 134.0),   # swing high 135.0 (H1)
    (134.0, 134.5, 131.0, 132.0),
    (132.0, 133.0, 130.0, 131.0),   # confirms H1
    (131.0, 131.5, 124.0, 125.0),   # swing low 124.0 (L1)
    (125.0, 127.0, 124.5, 126.5),
    (126.5, 128.0, 125.0, 127.5),   # confirms L1
    (127.5, 130.0, 126.0, 129.0),   # swing high 130.0 (H2, lower high)
    (129.0, 129.5, 125.5, 126.0),
    (126.0, 127.0, 122.0, 123.0),   # confirms H2; low 122 SWEEPS L1@124
    (123.0, 124.0, 118.0, 119.0),   # swing low 118.0 (L2, lower low)
    (119.0, 122.0, 118.5, 121.0),
    (121.0, 123.0, 119.5, 122.5),   # confirms L2 → bias bearish, DOL = 118
    (122.5, 124.0, 121.0, 123.0),
    (123.0, 125.0, 122.0, 124.5),
    (124.5, 126.0, 123.5, 125.0),
    (125.0, 126.5, 124.0, 125.5),
    (125.5, 126.0, 124.0, 124.5),
    (124.5, 125.5, 123.0, 124.0),
]

LTF_CORE_BEAR = [
    (124.1, 124.4, 123.8, 124.0),
    (124.0, 124.2, 123.6, 123.8),
    (123.8, 124.3, 123.5, 124.1),
    (124.1, 124.8, 123.9, 124.6),
    (124.6, 125.2, 124.3, 124.9),   # inducement swing HIGH 125.2
    (124.9, 125.0, 124.2, 124.4),
    (124.4, 124.6, 123.8, 124.0),   # confirms the swing high
    (124.0, 124.2, 123.4, 123.6),
    (123.6, 123.8, 123.0, 123.2),
    (123.2, 125.6, 123.1, 125.0),   # SWEEP bar: wick to 125.6 > 125.2
    (125.0, 125.1, 123.3, 123.9),   # CSD bar: close 123.9 < 124.35 (50pct only)
    (123.9, 124.0, 122.8, 123.0),   # entry bar: Entry 1 fills at open 123.9
    (123.0, 123.4, 122.6, 122.8),
    (122.8, 123.0, 122.4, 122.6),
]


def _warmdown(n: int, hi: float, step: float) -> list[tuple]:
    """Monotonically falling bars — the descending counterpart of _warmup;
    strictly falling highs AND lows can never print a fractal swing."""
    return [(hi - step * i - 0.1, hi - step * i, hi - step * i - 0.4,
             hi - step * i - 0.2) for i in range(n)]


def htf_frame_bear() -> pd.DataFrame:
    return _to_frame(HTF_BARS_BEAR, _START, timedelta(hours=1))


def ltf_frame_bear(drift_bars: int = 20) -> pd.DataFrame:
    warm = _warmdown(20, 126.3, 0.1)   # falls to high 124.4/low 124.0, flush with core
    last_close = LTF_CORE_BEAR[-1][3]
    drift = _warmdown(drift_bars, last_close + 0.2, 0.05)  # keeps falling, stays > 118
    start = _START + timedelta(hours=len(HTF_BARS_BEAR))
    return _to_frame(warm + LTF_CORE_BEAR + drift, start, timedelta(minutes=5))


def ltf_frame_mss_only() -> pd.DataFrame:
    """Variant: price consolidates (printing a swing high), then closes above
    it — a clean bullish MSS — WITHOUT ever sweeping the inducement low.
    The engine must produce zero signals (MSS is never an entry trigger)."""
    core = [
        (104.0, 104.3, 103.8, 104.2),
        (104.2, 104.5, 104.0, 104.4),
        (104.4, 104.8, 104.2, 104.6),  # swing high 104.8
        (104.6, 104.7, 104.3, 104.5),
        (104.5, 104.6, 104.1, 104.3),  # swing low 104.1 (inducement candidate)
        (104.3, 104.5, 104.2, 104.4),  # confirms the 104.8 high
        (104.4, 104.6, 104.3, 104.5),  # confirms the 104.1 low
        (104.5, 104.7, 104.4, 104.6),
        (104.6, 105.2, 104.5, 105.1),  # MSS: body close above 104.8 — no sweep happened
        (105.1, 105.3, 104.9, 105.2),
        (105.2, 105.4, 105.0, 105.3),
    ]
    return ltf_frame(core=core, drift_bars=5)
