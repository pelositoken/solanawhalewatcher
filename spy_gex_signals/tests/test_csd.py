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


# Sweep candle: o=104.6 h=104.7 l=102.6 c=103.0 → range mid 103.65, body mid 103.8
SWEEP_BAR = bar(9, 104.6, 104.7, 102.6, 103.0)


def test_50pct_range_midpoint_fires_on_body_close():
    cfg = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    confirm = bar(10, 103.0, 104.9, 102.9, 104.2)  # close 104.2 > 103.65
    ev = check_csd(confirm, SWEEP_BAR, sweep_long(), SWEEP_BAR, cfg)
    assert ev is not None and ev.rule_fired == "50pct"
    assert ev.threshold_50pct == pytest.approx(103.65)


def test_wick_above_midpoint_never_triggers():
    cfg = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    confirm = bar(10, 103.0, 104.9, 102.9, 103.4)  # HIGH clears 103.65, close doesn't
    assert check_csd(confirm, SWEEP_BAR, sweep_long(), SWEEP_BAR, cfg) is None


def test_body_midpoint_mode_is_stricter_here():
    # close 103.7: above range mid (103.65) but below body mid (103.8)
    confirm = bar(10, 103.0, 104.0, 102.9, 103.7)
    cfg_range = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    cfg_body = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="body")
    assert check_csd(confirm, SWEEP_BAR, sweep_long(), SWEEP_BAR, cfg_range) is not None
    assert check_csd(confirm, SWEEP_BAR, sweep_long(), SWEEP_BAR, cfg_body) is None


def test_prior_candle_rule_body_scope():
    cfg = StructureConfig(csd_rule="prior_candle", prior_candle_scope="body")
    prior = bar(10, 103.0, 104.0, 102.9, 103.5)   # body high 103.5
    confirm = bar(11, 103.5, 103.8, 103.3, 103.6)  # close 103.6 > 103.5
    ev = check_csd(confirm, prior, sweep_long(), SWEEP_BAR, cfg)
    assert ev is not None and ev.rule_fired == "prior_candle"


def test_prior_candle_rule_full_scope_needs_wick_cleared():
    cfg = StructureConfig(csd_rule="prior_candle", prior_candle_scope="full")
    prior = bar(10, 103.0, 104.0, 102.9, 103.5)    # full high 104.0
    confirm = bar(11, 103.5, 103.9, 103.3, 103.6)  # close 103.6 < 104.0
    assert check_csd(confirm, prior, sweep_long(), SWEEP_BAR, cfg) is None
    confirm2 = bar(11, 103.5, 104.3, 103.3, 104.1)  # close 104.1 > 104.0
    assert check_csd(confirm2, prior, sweep_long(), SWEEP_BAR, cfg) is not None


def test_both_mode_reports_which_rules_fired():
    cfg = StructureConfig(csd_rule="both")
    confirm = bar(10, 103.0, 105.0, 102.9, 104.8)  # clears mid AND sweep-bar body high
    ev = check_csd(confirm, SWEEP_BAR, sweep_long(), SWEEP_BAR, cfg)
    assert ev is not None and ev.rule_fired == "50pct+prior_candle"


def test_short_direction_mirror():
    # Bearish sweep candle: spiked up to 106.0, inducement was a swing high
    sweep_bar = bar(9, 104.0, 106.0, 103.9, 105.5)  # range mid 104.95
    sw = Swing(SwingKind.HIGH, 105.4, 4, TS, 6)
    sweep = SweepEvent(Direction.SHORT, sw, 9, 106.0, 9)
    cfg = StructureConfig(csd_rule="50pct", sweep_candle_midpoint="range")
    confirm = bar(10, 105.5, 105.6, 104.0, 104.2)  # close 104.2 < 104.95
    ev = check_csd(confirm, sweep_bar, sweep, sweep_bar, cfg)
    assert ev is not None and ev.rule_fired == "50pct"
    wick_only = bar(10, 105.5, 105.7, 104.0, 105.2)  # low pokes below, close doesn't
    assert check_csd(wick_only, sweep_bar, sweep, sweep_bar, cfg) is None
