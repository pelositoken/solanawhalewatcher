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
import json
import sys
from datetime import datetime, timezone
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


def write_report(cfg, title: str, slug: str, result, extra_note: str = "") -> Path:
    """Full chronological eyeball-pass report: every decision the engine made,
    in order, with the data it used — for review against the user's own charts
    BEFORE any backtest quantifies an 'edge' on top of it."""
    st = cfg.structure
    lines = [
        f"# Structure engine eyeball report — {title}",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Config: swing_strength={st.swing_strength}, csd_rule={st.csd_rule}, "
        f"midpoint={st.sweep_candle_midpoint}, prior_scope={st.prior_candle_scope} "
        f"(prior_candle measures the SWEEP candle, clamped ≥ 50% midpoint), "
        f"entry_type={st.entry_type}, max_bars_sweep_to_csd={st.max_bars_sweep_to_csd}, "
        f"invalidation_buffer={st.invalidation_buffer_pct}",
        f"- GEX alignment: not_wired (Phase 4). SMT statuses are real per-signal "
        f"reads when a correlated feed is present; 'not_available' means no data, "
        f"never a silent pass.",
    ]
    if extra_note:
        lines.append(f"- {extra_note}")
    lines += ["", "## Summary", "",
              f"- signals emitted: **{len(result.signals)}**",
              f"- MSS events (annotations only, never entries): {result.mss_event_count}"]
    for k in sorted(result.counters):
        lines.append(f"- {k}: {result.counters[k]}")

    lines += ["", "## Signals", ""]
    if not result.signals:
        lines.append("_none_")
    for i, s in enumerate(result.signals, 1):
        risk = abs((s.entry or 0) - s.stop) if s.entry else None
        lines += [
            f"### Signal {i}: {s.direction.value.upper()} @ {s.created_ts}",
            "",
            f"| field | value |", f"|---|---|",
            f"| entry ({s.entry_type}, {s.status}) | {s.entry} |",
            f"| stop (beyond sweep extreme) | {s.stop:.4f} |",
            f"| 3R management target | {s.target_3r and round(s.target_3r, 4)} |",
            f"| inducement level | {s.inducement_level} |",
            f"| sweep extreme | {s.sweep_extreme} |",
            f"| CSD rule fired | {s.csd_rule_fired} |",
            f"| DOL (thesis) | {s.dol_level} "
            f"({s.dol_r_multiple and round(s.dol_r_multiple, 2)}R stretch) |",
            f"| SMT | {s.smt_status} |",
            f"| high visibility | {s.high_visibility} |",
            f"| GEX alignment | {s.gex_alignment} |",
            "",
        ]

    lines += ["", "## Chronological decision trail", "",
              "| ts | event | detail | output |", "|---|---|---|---|"]
    for e in result.events:
        inp = dict(e["inputs"])
        ts = inp.pop("bar_ts", inp.pop("ltf_bar_ts", inp.pop("csd_bar_ts", "")))
        inp.pop("instrument", None)
        inp.pop("tf_pair", None)
        detail = json.dumps(inp, default=str)
        output = json.dumps(e["output"], default=str)
        if len(detail) > 220:
            detail = detail[:217] + "..."
        if len(output) > 160:
            output = output[:157] + "..."
        lines.append(f"| {ts} | {e['rule']} | `{detail}` | `{output}` |")

    reports_dir = cfg.resolve_path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"structure_scan_{slug}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.md"
    out.write_text("\n".join(lines))
    print(f"  [report → {out}]")
    return out


def run_fixture(cfg, dlog, report: bool) -> None:
    smt = SmtChecker(
        SmtConfig(enabled=True, min_correlation=0.7, correlation_lookback_bars=40),
        cfg.structure.swing_strength,
        primary_df=fixtures.ltf_frame(),
        correlated_df=fixtures.correlated_frame(),
        correlated_symbol="SYNTH-CORR",
    )
    engine = StructureEngine("SYNTH-LONG", "1h", "5m", cfg.structure,
                             decision_logger=dlog, smt_checker=smt)
    result = engine.run(fixtures.htf_frame(), fixtures.ltf_frame())
    print_result("SYNTH long fixture (1h/5m) — expected: exactly 1 LONG, CSD rule "
                 "50pct, entry 104.2, DOL 112, SMT divergence", result)
    if report:
        write_report(cfg, "SYNTH-LONG 1h/5m fixture", "SYNTH-LONG_1h-5m", result,
                     extra_note="Deterministic synthetic scenario, not market data.")

    engine_s = StructureEngine("SYNTH-SHORT", "1h", "5m", cfg.structure,
                               decision_logger=dlog)
    result_s = engine_s.run(fixtures.htf_frame_bear(), fixtures.ltf_frame_bear())
    print_result("SYNTH short fixture (1h/5m) — expected: exactly 1 SHORT, CSD rule "
                 "50pct, entry 123.9, stop 125.6628, 3R 118.6116, DOL 118", result_s)
    if report:
        write_report(cfg, "SYNTH-SHORT 1h/5m fixture", "SYNTH-SHORT_1h-5m", result_s,
                     extra_note="Deterministic synthetic scenario, not market data.")


def run_live(cfg, dlog, only_symbol: str | None, report: bool) -> None:
    provider = make_price_provider(cfg.price.provider, cfg)
    frames: dict[tuple[str, str], object] = {}
    rolls: dict = {}   # continuous-futures roll timestamps per (symbol, tf)

    def frame(symbol: str, tf: str):
        if (symbol, tf) not in frames:
            frames[(symbol, tf)] = provider.get_history(symbol, tf)
            rolls[(symbol, tf)] = provider.get_roll_dates(symbol)
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
                corr_df = frame(corr_symbol, ltf)
                smt = SmtChecker(cfg.structure.smt, cfg.structure.swing_strength,
                                 primary_df=ltf_df, correlated_df=corr_df,
                                 correlated_symbol=corr_symbol,
                                 roll_dates=rolls.get((corr_symbol, ltf), []))
            engine = StructureEngine(name, htf, ltf, cfg.structure,
                                     decision_logger=dlog, smt_checker=smt)
            result = engine.run(htf_df, ltf_df)
            span = f"{ltf_df.index[0].date()} → {ltf_df.index[-1].date()}"
            print_result(f"{name} ({htf}/{ltf}, {len(ltf_df)} LTF bars {span})", result)
            if report:
                write_report(cfg, f"{name} {htf}/{ltf} ({span})",
                             f"{name}_{htf}-{ltf}", result,
                             extra_note=f"Data: {cfg.price.provider}, LTF bars: "
                                        f"{len(ltf_df)}, span {span}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="Restrict to one configured instrument")
    parser.add_argument("--fixture", action="store_true",
                        help="Run the deterministic synthetic scenarios (no network)")
    parser.add_argument("--report", action="store_true",
                        help="Write a full markdown eyeball report (signals + every "
                             "rejection + chronological decision trail) to reports/")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(cfg.resolve_path(cfg.logging.dir))
    dlog = get_decision_logger(cfg.resolve_path(cfg.logging.decision_log))

    if args.fixture:
        run_fixture(cfg, dlog, args.report)
    else:
        run_live(cfg, dlog, args.symbol, args.report)
    print(f"\n[decision log: {dlog.path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
