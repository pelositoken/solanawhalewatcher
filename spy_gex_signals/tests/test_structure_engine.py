"""End-to-end engine tests on the hand-crafted fixture scenarios.

The scenarios have known geometry (see fixtures.py docstring), so these
tests pin down exact entry/stop/target arithmetic and — most importantly —
the framework's guardrails: MSS alone never signals, wicks never confirm,
unconfirmed sweeps expire.
"""

import pytest

from spy_gex_signals.config import SmtConfig, StructureConfig
from spy_gex_signals.structure import fixtures
from spy_gex_signals.structure.engine import StructureEngine
from spy_gex_signals.structure.models import Direction
from spy_gex_signals.structure.smt import SmtChecker

CFG = StructureConfig()  # defaults: csd_rule=both, midpoint=range, entry immediate, k=2


def make_engine(cfg=CFG, with_smt=False):
    smt = None
    if with_smt:
        smt = SmtChecker(SmtConfig(enabled=True, min_correlation=0.7,
                                   correlation_lookback_bars=40),
                         cfg.swing_strength, fixtures.ltf_frame(),
                         fixtures.correlated_frame(), "CORR")
    return StructureEngine("SYNTH", "1h", "5m", cfg, smt_checker=smt)


def test_full_long_scenario_emits_exactly_one_signal():
    result = make_engine(with_smt=True).run(fixtures.htf_frame(), fixtures.ltf_frame())
    assert len(result.signals) == 1
    s = result.signals[0]

    assert s.direction == Direction.LONG
    assert s.status == "filled"
    assert s.entry == pytest.approx(104.2)               # next bar open after CSD
    assert s.inducement_level == pytest.approx(103.0)
    assert s.sweep_extreme == pytest.approx(102.6)
    assert s.stop == pytest.approx(102.6 * (1 - 0.0005))  # just beyond the sweep
    risk = s.entry - s.stop
    assert s.target_3r == pytest.approx(s.entry + 3 * risk)  # 3R is management...
    assert s.dol_level == pytest.approx(112.0)               # ...DOL is thesis
    assert s.dol_r_multiple == pytest.approx((112.0 - s.entry) / risk)
    assert s.csd_rule_fired == "50pct"    # close beat range mid but NOT prior body
    assert s.smt_status == "divergence"
    assert s.gex_alignment == "not_wired"
    assert s.subtf_confirmation == "not_evaluated"


def test_dol_skips_swept_htf_level():
    # The 105 HTF high was swept by the 112 leg — DOL must be 112, never 105.
    result = make_engine().run(fixtures.htf_frame(), fixtures.ltf_frame())
    assert result.signals[0].dol_level == pytest.approx(112.0)


def test_unconfirmed_sweep_expires_without_signal():
    result = make_engine().run(fixtures.htf_frame(), fixtures.ltf_frame_no_csd())
    assert len(result.signals) == 0
    assert result.counters.get("sweeps_detected", 0) == 1
    assert result.counters.get("rejected_expired_no_csd", 0) == 1


def test_mss_alone_never_signals():
    # Clean bullish MSS with no inducement sweep: zero signals, MSS annotated.
    result = make_engine().run(fixtures.htf_frame(), fixtures.ltf_frame_mss_only())
    assert len(result.signals) == 0
    assert result.counters.get("sweeps_detected", 0) == 0
    assert result.mss_event_count >= 1


def test_fvg_retest_entry_type_rejects_when_no_gap():
    # The fixture's confirmation move leaves no FVG (bars overlap), so Entry 2
    # produces no trade — per spec, the trade runs without us.
    cfg = StructureConfig(entry_type="fvg_retest")
    result = make_engine(cfg).run(fixtures.htf_frame(), fixtures.ltf_frame())
    assert len(result.signals) == 0
    assert result.counters.get("rejected_no_fvg_for_retest", 0) == 1


def test_prior_candle_measures_against_sweep_candle():
    # The first recovery close (104.2) doesn't clear the SWEEP candle's body
    # high (104.6), so prior_candle can't fire where 50pct did; the next
    # close (104.8) clears 104.6 and confirms.
    cfg = StructureConfig(csd_rule="prior_candle")
    result = make_engine(cfg).run(fixtures.htf_frame(), fixtures.ltf_frame())
    assert len(result.signals) == 1
    s = result.signals[0]
    assert s.csd_rule_fired == "prior_candle"
    assert s.entry == pytest.approx(104.8)


def test_prior_candle_ignores_small_interim_bodies():
    # Regression for review point 1: confirmation 3 bars after the sweep,
    # through small interim bodies. Bar +2 closes 103.4 — above the interim
    # bar's body high (103.2), which the old literal-predecessor semantics
    # confirmed on — but far below the sweep candle's 104.6, so it must NOT
    # confirm. Bar +3 closes 104.8 > 104.6 and does.
    cfg = StructureConfig(csd_rule="prior_candle")
    ltf = fixtures.ltf_frame_slow_recovery()
    result = make_engine(cfg).run(fixtures.htf_frame(), ltf)
    assert len(result.signals) == 1
    s = result.signals[0]
    assert s.csd_rule_fired == "prior_candle"
    assert s.created_ts == ltf.index[20 + 12].to_pydatetime()  # bar +3, NOT bar +2
    assert s.entry == pytest.approx(104.8)


def test_full_short_scenario_hand_computed_values():
    # Independent short-side scenario (review point 4) — expected values are
    # hand-derived in fixtures.py, not mirrored from the long path.
    result = make_engine().run(fixtures.htf_frame_bear(), fixtures.ltf_frame_bear())
    assert len(result.signals) == 1
    s = result.signals[0]
    assert s.direction == Direction.SHORT
    assert s.status == "filled"
    assert s.inducement_level == pytest.approx(125.2)
    assert s.sweep_extreme == pytest.approx(125.6)
    assert s.csd_rule_fired == "50pct"            # 123.9 < range mid 124.35
    assert s.entry == pytest.approx(123.9)        # next bar open
    assert s.stop == pytest.approx(125.6628)      # 125.6 × 1.0005
    risk = s.stop - s.entry
    assert risk == pytest.approx(1.7628)
    assert s.target_3r == pytest.approx(118.6116)  # 123.9 − 3 × 1.7628
    assert s.dol_level == pytest.approx(118.0)     # swept 124 low must be skipped
    assert s.dol_r_multiple == pytest.approx((123.9 - 118.0) / 1.7628, rel=1e-6)
    assert s.smt_status == "not_available"         # no correlated feed wired here


def test_fvg_retest_fills_on_retrace():
    cfg = StructureConfig(entry_type="fvg_retest")
    result = make_engine(cfg).run(fixtures.htf_frame(), fixtures.ltf_frame_fvg_retest())
    assert len(result.signals) == 1
    s = result.signals[0]
    assert s.status == "filled"
    assert s.entry == pytest.approx(103.5)          # FVG upper edge [103.4, 103.5]
    assert s.stop == pytest.approx(102.6 * 0.9995)  # stop unchanged by entry type
    risk = s.entry - s.stop
    assert s.target_3r == pytest.approx(103.5 + 3 * risk)  # 3R from the FVG fill


def test_fvg_retest_expires_when_price_runs():
    cfg = StructureConfig(entry_type="fvg_retest")
    result = make_engine(cfg).run(fixtures.htf_frame(),
                                  fixtures.ltf_frame_fvg_retest(retrace=False))
    assert len(result.signals) == 0
    assert result.counters.get("fvg_entries_expired_unfilled", 0) == 1


def test_subtf_required_rejects_loudly_when_unplumbed():
    cfg = StructureConfig(require_subtf_confirmation=True)
    result = make_engine(cfg).run(fixtures.htf_frame(), fixtures.ltf_frame())
    assert len(result.signals) == 0
    assert result.counters.get("rejected_subtf_required_but_unavailable", 0) == 1
