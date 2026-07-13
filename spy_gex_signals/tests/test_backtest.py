from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from spy_gex_signals.backtest.structure_backtest import (
    compute_metrics, edge_verdict, run_pair_backtest, simulate_trade,
)
from spy_gex_signals.structure import fixtures
from spy_gex_signals.structure.models import Direction, StructureSignal

T0 = datetime(2026, 1, 6, tzinfo=timezone.utc)


def frame(bars):
    idx = pd.DatetimeIndex([T0 + timedelta(minutes=5 * i) for i in range(len(bars))])
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    return df


def make_signal(direction=Direction.LONG, entry=100.0, stop=99.0, fill_index=0):
    s = StructureSignal(
        instrument="T", tf_pair="1h/5m", direction=direction, created_ts=T0,
        entry_type="immediate", entry=None, stop=stop, target_3r=None,
        inducement_level=99.5, sweep_extreme=stop, csd_rule_fired="50pct",
        dol_level=None, dol_r_multiple=None, smt_status="not_available",
        high_visibility=False, subtf_confirmation="not_evaluated")
    s.finalize_entry(entry)
    s.filled_bar_index = fill_index
    return s


def test_long_win_hits_3r_target():
    # entry 100, stop 99 → target 103
    df = frame([(100, 100.5, 99.5, 100.2),
                (100.2, 101.5, 100.0, 101.2),
                (101.2, 103.5, 101.0, 103.2)])   # high 103.5 ≥ 103
    t = simulate_trade(make_signal(), df)
    assert t.outcome == "win" and t.r_multiple == 3.0 and t.exit_index == 2


def test_long_loss_hits_stop():
    df = frame([(100, 100.5, 99.5, 100.2),
                (100.2, 100.4, 98.9, 99.0)])     # low 98.9 ≤ 99
    t = simulate_trade(make_signal(), df)
    assert t.outcome == "loss" and t.r_multiple == -1.0 and not t.ambiguous


def test_same_bar_ambiguity_scored_as_loss():
    df = frame([(100, 103.5, 98.5, 100.0)])      # touches 103 AND 99 in one bar
    t = simulate_trade(make_signal(), df)
    assert t.outcome == "loss" and t.ambiguous


def test_open_trade_when_data_ends():
    df = frame([(100, 100.5, 99.5, 100.2), (100.2, 100.8, 99.8, 100.5)])
    t = simulate_trade(make_signal(), df)
    assert t.outcome == "open" and t.r_multiple is None


def test_short_win_and_loss_mirror():
    # short: entry 100, stop 101 → target 97
    df_win = frame([(100, 100.5, 96.9, 97.0)])
    t = simulate_trade(make_signal(Direction.SHORT, 100.0, 101.0), df_win)
    assert t.outcome == "win"
    df_loss = frame([(100, 101.2, 99.5, 101.0)])
    t2 = simulate_trade(make_signal(Direction.SHORT, 100.0, 101.0), df_loss)
    assert t2.outcome == "loss"


def test_metrics_math():
    df = frame([(100, 103.5, 99.5, 103.2)])  # any bar; outcomes injected below
    trades = [simulate_trade(make_signal(), frame([(100, 103.5, 100.5, 103.2)])),  # win
              simulate_trade(make_signal(), frame([(100, 100.4, 98.9, 99.0)])),    # loss
              simulate_trade(make_signal(), frame([(100, 100.4, 98.9, 99.0)])),    # loss
              simulate_trade(make_signal(), frame([(100, 100.2, 99.9, 100.1)]))]   # open
    m = compute_metrics("T", 5, trades, {}, span_days=30.44)
    assert m.n_signals == 4 and m.n_closed == 3 and m.n_open == 1
    assert m.wins == 1 and m.losses == 2
    assert m.win_rate == pytest.approx(1 / 3)
    assert m.avg_r == pytest.approx((3 - 1 - 1) / 3)
    assert m.total_r == pytest.approx(1.0)
    # equity by exit order: +3 → 2 → 1: peak 3, trough 1 → max DD 2R
    assert m.max_drawdown_r == pytest.approx(2.0)
    assert m.trades_per_month == pytest.approx(4.0)


def test_edge_verdict_thresholds():
    def m(n_closed, wins):
        trades_r = [3.0] * wins + [-1.0] * (n_closed - wins)
        avg = sum(trades_r) / n_closed if n_closed else None
        from spy_gex_signals.backtest.structure_backtest import PairMetrics
        return PairMetrics("T", 5, n_closed, n_closed, 0, 0, wins,
                           n_closed - wins, wins / n_closed if n_closed else None,
                           avg, sum(trades_r) if n_closed else None, 1.0, 1.0, 30.0)
    assert "NO TRADES" in edge_verdict(m(0, 0))
    assert "INSUFFICIENT SAMPLE" in edge_verdict(m(10, 8))
    assert "POSSIBLE EDGE" in edge_verdict(m(40, 16))   # 40% wr > 25% breakeven
    assert "NO EDGE" in edge_verdict(m(40, 8))          # 20% wr < 25% breakeven


def test_pipeline_on_fixtures_entry1_enforced():
    # Both fixture scenarios produce 1 signal each; neither bracket resolves
    # within the short fixture data → open trades, zero closed.
    res = run_pair_backtest("SYNTH-LONG", "1h", "5m",
                            __import__("spy_gex_signals.config",
                                       fromlist=["StructureConfig"]).StructureConfig(
                                entry_type="fvg_retest"),  # must be overridden to immediate
                            fixtures.htf_frame(), fixtures.ltf_frame(),
                            max_bars_values=(5,))
    m = res[0]
    assert m.n_signals == 1 and m.n_open == 1 and m.n_closed == 0
    assert m.trades[0].signal.entry_type == "immediate"  # Entry-1 enforced
