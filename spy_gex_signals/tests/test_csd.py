from datetime import datetime, timezone

import pytest

from spy_gex_signals.config import StructureConfig
from spy_gex_signals.structure.csd import check_csd
from spy_gex_signals.structure.models import Bar, Direction, SweepEvent, Swing, SwingKind

TS = datetime(2026, 1, 5, tzinfo=timezone.utc)


def bar(i, o, h, l, c):
    return Bar(index=i, ts=TS, open=o, high=h, low=l, close=c)


def sweep_long(inducement=103.0, extreme=102.6):
    sw = Swing(SwingKind.LOW, inducement, 4, TS, 6)
    return SweepEvent(Direction.LONG, sw, 9, extreme, 9)


# Sweep candle: o=104.6 h=104.7 l=102.6 c=103.0 → range mid 103.65, body mid 103.8,
# body high 104.6, full high 104.7
SWEEP_BAR = bar(9, 104.6, 104.7, 102.6, 103.0)


def test_50pct_range_midpoint_fires_on_body_close():
    cfg = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    confirm = bar(10, 103.0, 104.9, 102.9, 104.2)  # close 104.2 > 103.65
    ev = check_csd(confirm, sweep_long(), SWEEP_BAR, cfg)
    assert ev is not None and ev.rule_fired == "50pct"
    assert ev.threshold_50pct == pytest.approx(103.65)


def test_wick_above_midpoint_never_triggers():
    cfg = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    confirm = bar(10, 103.0, 104.9, 102.9, 103.4)  # HIGH clears 103.65, close doesn't
    assert check_csd(confirm, sweep_long(), SWEEP_BAR, cfg) is None


def test_body_midpoint_mode_is_stricter_here():
    # close 103.7: above range mid (103.65) but below body mid (103.8)
    confirm = bar(10, 103.0, 104.0, 102.9, 103.7)
    cfg_range = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    cfg_body = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="body")
    assert check_csd(confirm, sweep_long(), SWEEP_BAR, cfg_range) is not None
    assert check_csd(confirm, sweep_long(), SWEEP_BAR, cfg_body) is None


def test_prior_candle_resolves_to_sweep_candle_body():
    # Threshold is the SWEEP candle's body high (104.6), regardless of what
    # bar immediately precedes the confirming candle.
    cfg = StructureConfig(csd_rule="prior_candle", prior_candle_scope="body")
    below = bar(12, 103.4, 104.5, 103.3, 104.2)   # 104.2 < 104.6 → no
    assert check_csd(below, sweep_long(), SWEEP_BAR, cfg) is None
    above = bar(12, 103.4, 104.9, 103.3, 104.8)   # 104.8 > 104.6 → fires
    ev = check_csd(above, sweep_long(), SWEEP_BAR, cfg)
    assert ev is not None and ev.rule_fired == "prior_candle"
    assert ev.threshold_prior == pytest.approx(104.6)
    assert ev.threshold_prior_boundary == pytest.approx(104.6)


def test_prior_candle_full_scope_needs_sweep_wick_cleared():
    cfg = StructureConfig(csd_rule="prior_candle", prior_candle_scope="full")
    between = bar(12, 103.4, 104.9, 103.3, 104.65)  # > body 104.6, < full 104.7
    assert check_csd(between, sweep_long(), SWEEP_BAR, cfg) is None
    above = bar(12, 103.4, 105.0, 103.3, 104.9)     # 104.9 > 104.7 → fires
    ev = check_csd(above, sweep_long(), SWEEP_BAR, cfg)
    assert ev is not None and ev.threshold_prior == pytest.approx(104.7)


def test_prior_candle_never_weaker_than_50pct():
    # Sweep candle with a huge upper wick: body high (103.4) sits BELOW the
    # range midpoint (106.3). The clamp must hold the threshold at 106.3 so
    # prior_candle can't fire on a close the 50% rule would reject.
    wicky_sweep = bar(9, 103.0, 110.0, 102.6, 103.4)
    cfg = StructureConfig(csd_rule="prior_candle", prior_candle_scope="body")
    weak = bar(10, 103.4, 104.5, 103.2, 104.0)   # > body high, < range mid → no
    assert check_csd(weak, sweep_long(), wicky_sweep, cfg) is None
    strong = bar(10, 103.4, 107.0, 103.2, 106.5)  # > 106.3 → fires
    ev = check_csd(strong, sweep_long(), wicky_sweep, cfg)
    assert ev is not None
    assert ev.threshold_prior == pytest.approx(106.3)          # clamped
    assert ev.threshold_prior_boundary == pytest.approx(103.4)  # raw body boundary


def test_both_mode_reports_which_rules_fired():
    cfg = StructureConfig(csd_rule="both")
    confirm = bar(10, 103.0, 105.0, 102.9, 104.8)  # clears mid AND sweep body high
    ev = check_csd(confirm, sweep_long(), SWEEP_BAR, cfg)
    assert ev is not None and ev.rule_fired == "50pct+prior_candle"


def test_short_direction_mirror():
    # Bearish sweep candle: spiked up to 106.0, inducement was a swing high
    sweep_bar = bar(9, 104.0, 106.0, 103.9, 105.5)  # range mid 104.95, body low 104.0
    sw = Swing(SwingKind.HIGH, 105.4, 4, TS, 6)
    sweep = SweepEvent(Direction.SHORT, sw, 9, 106.0, 9)
    cfg = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    confirm = bar(10, 105.5, 105.6, 104.0, 104.2)  # close 104.2 < 104.95
    ev = check_csd(confirm, sweep, sweep_bar, cfg)
    assert ev is not None and ev.rule_fired == "50pct"
    wick_only = bar(10, 105.5, 105.7, 104.0, 105.2)  # low pokes below, close doesn't
    assert check_csd(wick_only, sweep, sweep_bar, cfg) is None
    # Short-side prior_candle: clamp = min(body low 104.0, mid 104.95) = 104.0
    cfg_prior = StructureConfig(csd_rule="prior_candle", prior_candle_scope="body")
    not_enough = bar(11, 104.2, 104.4, 104.05, 104.1)  # 104.1 > 104.0 → no
    assert check_csd(not_enough, sweep, sweep_bar, cfg_prior) is None
    enough = bar(11, 104.2, 104.3, 103.5, 103.8)       # 103.8 < 104.0 → fires
    ev2 = check_csd(enough, sweep, sweep_bar, cfg_prior)
    assert ev2 is not None and ev2.threshold_prior == pytest.approx(104.0)
