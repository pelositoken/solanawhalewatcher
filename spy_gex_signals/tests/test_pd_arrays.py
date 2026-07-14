from datetime import datetime, timezone

from spy_gex_signals.structure.models import Bar, Direction, Swing, SwingKind
from spy_gex_signals.structure.pd_arrays import detect_fvg, is_no_wick, trendline_liquidity

TS = datetime(2026, 1, 5, tzinfo=timezone.utc)


def bar(i, o, h, l, c):
    return Bar(index=i, ts=TS, open=o, high=h, low=l, close=c)


def test_bullish_fvg():
    b1 = bar(0, 100, 101.0, 99.5, 100.8)
    b2 = bar(1, 100.8, 103.0, 100.7, 102.8)   # displacement candle
    b3 = bar(2, 102.8, 104.0, 102.0, 103.5)   # low 102.0 > b1 high 101.0
    fvg = detect_fvg(b1, b2, b3)
    assert fvg is not None and fvg.direction == Direction.LONG
    assert fvg.lower == 101.0 and fvg.upper == 102.0


def test_bearish_fvg():
    b1 = bar(0, 100, 100.5, 99.0, 99.2)
    b2 = bar(1, 99.2, 99.3, 96.0, 96.2)
    b3 = bar(2, 96.2, 97.0, 95.0, 96.5)       # high 97.0 < b1 low 99.0
    fvg = detect_fvg(b1, b2, b3)
    assert fvg is not None and fvg.direction == Direction.SHORT
    assert fvg.upper == 97.0 and fvg.lower == 99.0


def test_no_fvg_when_overlapping():
    b1 = bar(0, 100, 101.0, 99.5, 100.8)
    b2 = bar(1, 100.8, 101.5, 100.0, 101.2)
    b3 = bar(2, 101.2, 102.0, 100.5, 101.8)   # low 100.5 < b1 high 101.0
    assert detect_fvg(b1, b2, b3) is None


def test_no_wick_detection():
    assert is_no_wick(bar(0, 100.0, 102.0, 100.0, 102.0), "bottom")   # open == low
    assert is_no_wick(bar(0, 100.0, 102.0, 100.0, 102.0), "top")      # close == high
    assert not is_no_wick(bar(0, 100.5, 102.0, 100.0, 101.0), "bottom")


def test_trendline_liquidity_on_aligned_swings():
    swings = [Swing(SwingKind.LOW, 100.0, 0, TS, 2),
              Swing(SwingKind.LOW, 101.0, 10, TS, 12),
              Swing(SwingKind.LOW, 102.0, 20, TS, 22)]
    tql = trendline_liquidity(swings)
    assert tql is not None and tql["kind"] == "low"
    assert tql["slope_per_bar"] == 0.1


def test_trendline_liquidity_rejects_scattered_swings():
    swings = [Swing(SwingKind.LOW, 100.0, 0, TS, 2),
              Swing(SwingKind.LOW, 108.0, 10, TS, 12),
              Swing(SwingKind.LOW, 102.0, 20, TS, 22)]
    assert trendline_liquidity(swings) is None
