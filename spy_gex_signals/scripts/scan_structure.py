#!/usr/bin/env python3
"""Run the Phase 2 structure engine over history and report every signal it
would have emitted, plus counts of setups considered and rejected (and why).

This is a signal DEMO, not a backtest — outcome simulation and metrics are
Phase 3.

Usage:
    python scripts/scan_structure.py                 # all configured instruments/pairs (needs network)
    python scripts/scan_structure.py --symbol SPY
    python scripts/scan_structure.py --fixture       # deterministic synthetic scenario, no network
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spy_gex_signals.config import SmtConfig, load_config
from spy_gex_signals.data.price_provider import make_price_provider
from spy_gex_signals.decision_log import get_decision_logger
from spy_gex_signals.logging_setup import setup_logging
from spy_gex_signals.structure import fixtures
from spy_gex_signals.structure.engine import StructureEngine
from spy_gex_signals.structure.smt import SmtChecker


def print_result(title: str, result) -> None:
    print(f"\n=== {title} ===")
    print(f"signals emitted: {len(result.signals)} | MSS events (annotations only): "
          f"{result.mss_event_count}")
    if result.counters:
        print("counters:")
        for k in sorted(result.counters):
            print(f"  {k:<40} {result.counters[k]}")
    for s in result.signals:
        stretch = f"{s.dol_r_multiple:.1f}R" if s.dol_r_multiple is not None else "n/a"
        print(f"\n  {s.direction.value.upper()} {s.instrument} [{s.tf_pair}] @ {s.created_ts}")
        print(f"    entry {s.entry} ({s.entry_type}, {s.status}) | stop {s.stop:.4f} "
              f"| 3R target {s.target_3r and round(s.target_3r, 4)}")
        print(f"    inducement {s.inducement_level} swept to {s.sweep_extreme} "
              f"| CSD rule: {s.csd_rule_fired}")
        print(f"    DOL {s.dol_level} (thesis stretch {stretch}) | SMT: {s.smt_status} "
              f"| high-visibility: {s.high_visibility} | GEX: {s.gex_alignment}")


def run_fixture(cfg, dlog) -> None:
    smt = SmtChecker(
        SmtConfig(enabled=True, min_correlation=0.7, correlation_lookback_bars=40),
        cfg.structure.swing_strength,
        primary_df=fixtures.ltf_frame(),
        correlated_df=fixtures.correlated_frame(),
        correlated_symbol="SYNTH-CORR",
    )
    engine = StructureEngine("SYNTH", "1h", "5m", cfg.structure,
                             decision_logger=dlog, smt_checker=smt)
    result = engine.run(fixtures.htf_frame(), fixtures.ltf_frame())
    print_result("SYNTH fixture (1h/5m) — expected: exactly 1 LONG, CSD rule 50pct, "
                 "entry 104.2, DOL 112, SMT divergence", result)


def run_live(cfg, dlog, only_symbol: str | None) -> None:
    provider = make_price_provider(cfg.price.provider)
    frames: dict[tuple[str, str], object] = {}

    def frame(symbol: str, tf: str):
        if (symbol, tf) not in frames:
            frames[(symbol, tf)] = provider.get_history(symbol, tf)
        return frames[(symbol, tf)]

    for name, inst in cfg.instruments.items():
        if only_symbol and name.upper() != only_symbol.upper():
            continue
        corr_symbol = cfg.structure.smt.pairs.get(inst.price_symbol)
        for htf, ltf in cfg.structure.timeframe_pairs:
            ltf_df = frame(inst.price_symbol, ltf)
            htf_df = frame(inst.price_symbol, htf)
            if ltf_df.empty or htf_df.empty:
                print(f"\n=== {name} ({htf}/{ltf}) === SKIPPED: no data")
                continue
            smt = None
            if cfg.structure.smt.enabled and corr_symbol:
                smt = SmtChecker(cfg.structure.smt, cfg.structure.swing_strength,
                                 primary_df=ltf_df, correlated_df=frame(corr_symbol, ltf),
                                 correlated_symbol=corr_symbol)
            engine = StructureEngine(name, htf, ltf, cfg.structure,
                                     decision_logger=dlog, smt_checker=smt)
            result = engine.run(htf_df, ltf_df)
            print_result(f"{name} ({htf}/{ltf}, {len(ltf_df)} LTF bars "
                         f"{ltf_df.index[0].date()} → {ltf_df.index[-1].date()})", result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="Restrict to one configured instrument")
    parser.add_argument("--fixture", action="store_true",
                        help="Run the deterministic synthetic scenario (no network)")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(cfg.resolve_path(cfg.logging.dir))
    dlog = get_decision_logger(cfg.resolve_path(cfg.logging.decision_log))

    if args.fixture:
        run_fixture(cfg, dlog)
    else:
        run_live(cfg, dlog, args.symbol)
    print(f"\n[decision log: {dlog.path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
