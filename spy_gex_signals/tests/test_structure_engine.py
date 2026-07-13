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


def test_prior_candle_only_confirms_one_bar_later():
    # 'Prior candle' = the candle immediately preceding the CONFIRMING candle.
    # The first recovery close (104.2) doesn't clear the sweep candle's body
    # high (104.6), so prior_candle can't fire where 50pct did; the NEXT bar's
    # close (104.8) clears its predecessor's body high (104.2) and confirms.
    cfg = StructureConfig(csd_rule="prior_candle")
    result = make_engine(cfg).run(fixtures.htf_frame(), fixtures.ltf_frame())
    assert len(result.signals) == 1
    s = result.signals[0]
    assert s.csd_rule_fired == "prior_candle"
    assert s.entry == pytest.approx(104.8)  # fill one bar later than the 50pct path


def test_subtf_required_rejects_loudly_when_unplumbed():
    cfg = StructureConfig(require_subtf_confirmation=True)
    result = make_engine(cfg).run(fixtures.htf_frame(), fixtures.ltf_frame())
    assert len(result.signals) == 0
    assert result.counters.get("rejected_subtf_required_but_unavailable", 0) == 1
