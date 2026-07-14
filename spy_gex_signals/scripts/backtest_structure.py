#!/usr/bin/env python3
"""Phase 3 runner: structure-only backtest (no GEX), Entry-1 only.

Reports win rate, avg R, max drawdown, and trade frequency separately per
instrument and timeframe pair, at max_bars_sweep_to_csd = 3 / 5 / 8, and
prints the edge-checkpoint verdict.

Usage:
    python scripts/backtest_structure.py                 # all instruments (needs network)
    python scripts/backtest_structure.py --symbol SPY
    python scripts/backtest_structure.py --fixture       # synthetic pipeline smoke test
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spy_gex_signals.config import load_config
from spy_gex_signals.backtest.structure_backtest import render_report, run_pair_backtest
from spy_gex_signals.data.price_provider import make_price_provider
from spy_gex_signals.decision_log import get_decision_logger
from spy_gex_signals.logging_setup import setup_logging
from spy_gex_signals.structure import fixtures
from spy_gex_signals.structure.smt import SmtChecker

SENSITIVITY = (3, 5, 8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="Restrict to one configured instrument")
    parser.add_argument("--fixture", action="store_true",
                        help="Synthetic pipeline smoke test (no network)")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(cfg.resolve_path(cfg.logging.dir))
    dlog = get_decision_logger(cfg.resolve_path(cfg.logging.decision_log))

    all_metrics: dict = {}
    data_notes: list[str] = []

    if args.fixture:
        data_notes.append("SYNTHETIC FIXTURE DATA — pipeline smoke test, not a market result.")
        all_metrics["SYNTH-LONG 1h/5m"] = run_pair_backtest(
            "SYNTH-LONG", "1h", "5m", cfg.structure,
            fixtures.htf_frame(), fixtures.ltf_frame(),
            max_bars_values=SENSITIVITY, decision_logger=dlog)
        all_metrics["SYNTH-SHORT 1h/5m"] = run_pair_backtest(
            "SYNTH-SHORT", "1h", "5m", cfg.structure,
            fixtures.htf_frame_bear(), fixtures.ltf_frame_bear(),
            max_bars_values=SENSITIVITY, decision_logger=dlog)
    else:
        provider = make_price_provider(cfg.price.provider, cfg)
        frames: dict = {}
        rolls: dict = {}   # continuous-futures roll timestamps per (symbol, tf)

        def frame(symbol: str, tf: str):
            if (symbol, tf) not in frames:
                frames[(symbol, tf)] = provider.get_history(symbol, tf)
                rolls[(symbol, tf)] = provider.get_roll_dates(symbol)
            return frames[(symbol, tf)]

        for name, inst in cfg.instruments.items():
            if args.symbol and name.upper() != args.symbol.upper():
                continue
            corr_symbol = cfg.structure.smt.pairs.get(inst.price_symbol)
            for htf, ltf in cfg.structure.timeframe_pairs:
                htf_df, ltf_df = frame(inst.price_symbol, htf), frame(inst.price_symbol, ltf)
                if htf_df.empty or ltf_df.empty:
                    data_notes.append(f"{name} {htf}/{ltf}: SKIPPED, no data.")
                    continue
                data_notes.append(
                    f"{name} {htf}/{ltf}: {len(ltf_df)} LTF bars, "
                    f"{ltf_df.index[0].date()} → {ltf_df.index[-1].date()} "
                    f"({cfg.price.provider}).")

                def smt_factory(_ltf_df=ltf_df, _corr=corr_symbol, _ltf=ltf):
                    if not (cfg.structure.smt.enabled and _corr):
                        return None
                    corr_df = frame(_corr, _ltf)
                    return SmtChecker(cfg.structure.smt, cfg.structure.swing_strength,
                                      primary_df=_ltf_df, correlated_df=corr_df,
                                      correlated_symbol=_corr,
                                      roll_dates=rolls.get((_corr, _ltf), []))

                all_metrics[f"{name} {htf}/{ltf}"] = run_pair_backtest(
                    name, htf, ltf, cfg.structure, htf_df, ltf_df,
                    smt_checker_factory=smt_factory,
                    max_bars_values=SENSITIVITY, decision_logger=dlog,
                    roll_dates=rolls.get((inst.price_symbol, ltf), []))

    report = render_report(all_metrics, cfg.structure.max_bars_sweep_to_csd, data_notes)
    reports_dir = cfg.resolve_path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"phase3_structure_backtest_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.md"
    out.write_text(report)

    print(report)
    print(f"\n[report written to {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
