from datetime import datetime, timedelta, timezone

from spy_gex_signals.structure.models import Bar, Bias, SwingKind
from spy_gex_signals.structure.swings import MarketStructure, SwingDetector

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def bars(ohlc):
    return [Bar(index=i, ts=T0 + timedelta(minutes=5 * i), open=o, high=h, low=l, close=c)
            for i, (o, h, l, c) in enumerate(ohlc)]


def test_swing_high_detection_and_confirmation_lag():
    det = SwingDetector(strength=2)
    data = bars([
        (10, 11.0, 9.5, 10.5),
        (10.5, 11.5, 10.0, 11.0),
        (11.0, 13.0, 10.8, 12.5),   # swing high 13.0 at index 2
        (12.5, 12.8, 11.5, 12.0),
        (12.0, 12.2, 11.0, 11.5),
    ])
    confirmed = []
    for b in data:
        confirmed += det.update(b)
    assert len(confirmed) == 1
    s = confirmed[0]
    assert s.kind == SwingKind.HIGH and s.price == 13.0 and s.bar_index == 2
    # Only knowable k=2 bars after the extreme — the no-lookahead guarantee.
    assert s.confirmed_at_index == 4


def test_tie_blocks_swing():
    det = SwingDetector(strength=1)
    data = bars([(10, 12.0, 9, 11), (11, 12.0, 10, 11.5), (11.5, 11.8, 10.5, 11)])
    out = []
    for b in data:
        out += det.update(b)
    assert not any(s.kind == SwingKind.HIGH for s in out)  # equal highs: no fractal


def test_msu_dedup_keeps_more_extreme_same_kind():
    ms = MarketStructure(strength=1)
    # low, then two successive highs (second one higher) with no low between
    data = bars([
        (10, 10.5, 9.0, 10.0),
        (10, 10.2, 9.5, 10.0),   # swing low 9.0 at idx 0? strength1: idx1 center
        (10, 12.0, 9.8, 11.5),   # swing high candidate
        (11, 11.5, 10.5, 11.0),
        (11, 13.0, 10.8, 12.5),  # higher swing high
        (12, 12.2, 11.0, 11.5),
    ])
    for b in data:
        ms.update(b)
    highs = [s for s in ms.swings if s.kind == SwingKind.HIGH]
    assert len(highs) == 1 and highs[0].price == 13.0


def test_trend_classification():
    ms = MarketStructure(strength=1)
    # HH + HL sequence: L(9) H(12) L(10) H(13)
    data = bars([
        (10, 10.5, 9.5, 10),
        (10, 10.4, 9.0, 10),    # swing low 9
        (10, 12.0, 10.8, 11.5),  # swing high 12
        (11, 11.2, 10.0, 10.5),  # swing low 10
        (11, 13.0, 10.5, 12.5),  # swing high 13
        (12, 12.4, 11.5, 12.0),
    ])
    for b in data:
        ms.update(b)
    assert ms.trend() == Bias.BULLISH


def test_mss_detected_on_body_close_beyond_swing():
    ms = MarketStructure(strength=1)
    data = bars([
        (10, 10.5, 9.5, 10),
        (10, 12.0, 9.8, 11.0),   # swing high 12
        (11, 11.5, 10.5, 11.0),  # confirms it
        (11, 12.5, 10.8, 11.9),  # wick above 12, close below — NOT an MSS
        (11.9, 12.6, 11.5, 12.4),  # body close above 12 → bullish MSS
    ])
    for b in data:
        ms.update(b)
    assert len(ms.mss_events) == 1
    e = ms.mss_events[0]
    assert e["direction"] == "bullish" and e["break_index"] == 4
