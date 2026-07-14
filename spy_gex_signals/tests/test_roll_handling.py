"""Continuous-futures roll handling: roll gaps are splice artifacts and must
never be scored as trades or read as SMT divergence."""

from datetime import timedelta

import pandas as pd
import pytest

from spy_gex_signals.backtest.structure_backtest import run_pair_backtest, spans_roll
from spy_gex_signals.config import SmtConfig, StructureConfig
from spy_gex_signals.structure import fixtures
from spy_gex_signals.structure.models import Direction
from spy_gex_signals.structure.smt import SmtChecker

from .test_backtest import frame, make_signal  # reuse helpers

CFG = StructureConfig()


def fixture_signal_window():
    """The long fixture's single trade: sweep bar 29 → open at end of data."""
    ltf = fixtures.ltf_frame()
    return ltf, ltf.index[29], ltf.index[-1]


def test_trade_spanning_roll_is_excluded_and_counted():
    ltf, sweep_ts, end_ts = fixture_signal_window()
    roll_inside = sweep_ts + timedelta(minutes=10)
    res = run_pair_backtest("SYNTH", "1h", "5m", CFG,
                            fixtures.htf_frame(), ltf,
                            max_bars_values=(5,), roll_dates=[roll_inside])
    m = res[0]
    assert m.n_excluded_roll == 1
    assert m.n_signals == 0          # nothing scored


def test_roll_outside_trade_window_keeps_trade():
    ltf, sweep_ts, _ = fixture_signal_window()
    roll_before = sweep_ts - timedelta(hours=1)
    res = run_pair_backtest("SYNTH", "1h", "5m", CFG,
                            fixtures.htf_frame(), ltf,
                            max_bars_values=(5,), roll_dates=[roll_before])
    m = res[0]
    assert m.n_excluded_roll == 0
    assert m.n_signals == 1


def test_spans_roll_uses_first_sweep_to_exit_window():
    df = frame([(100, 100.5, 99.5, 100.2), (100.2, 103.5, 100.1, 103.2)])
    from spy_gex_signals.backtest.structure_backtest import simulate_trade
    sig = make_signal()                      # first_sweep_ts = T0
    trade = simulate_trade(sig, df)          # wins on bar 1
    inside = df.index[1] - timedelta(seconds=1)
    after_exit = df.index[1] + timedelta(minutes=30)
    assert spans_roll(trade, df.index, [inside]) is True
    assert spans_roll(trade, df.index, [after_exit]) is False
    assert spans_roll(trade, df.index, []) is False


SMT_CFG = SmtConfig(enabled=True, min_correlation=0.7, correlation_lookback_bars=40)


def sweep_ts():
    return fixtures.ltf_frame().index[20 + 9].to_pydatetime()


def test_smt_roll_in_check_window_reports_not_available():
    roll = pd.Timestamp(sweep_ts()) - pd.Timedelta(minutes=5)  # within last 3 bars
    checker = SmtChecker(SMT_CFG, 2, fixtures.ltf_frame(), fixtures.correlated_frame(),
                         "CORR", roll_dates=[roll])
    status, detail = checker.check(sweep_ts(), Direction.LONG)
    assert status == "not_available"
    assert detail["reason"] == "roll_gap"


def test_smt_roll_far_away_still_reads_divergence():
    roll = pd.Timestamp(sweep_ts()) - pd.Timedelta(hours=3)  # well outside window
    checker = SmtChecker(SMT_CFG, 2, fixtures.ltf_frame(), fixtures.correlated_frame(),
                         "CORR", roll_dates=[roll])
    status, _ = checker.check(sweep_ts(), Direction.LONG)
    assert status == "divergence"
