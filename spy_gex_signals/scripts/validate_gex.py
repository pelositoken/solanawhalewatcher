#!/usr/bin/env python3
"""Produce the Phase 1 GEX validation report for comparison against a
published SpotGamma/Tradytics chart.

Usage:
    python scripts/validate_gex.py --symbol SPY                # live fetch (also archived)
    python scripts/validate_gex.py --file data/chains/SPY/2026-07-13/193000Z.json.gz
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spy_gex_signals.config import load_config
from spy_gex_signals.data.archiver import ChainArchiver
from spy_gex_signals.data.chain_provider import FileChainProvider, make_chain_provider
from spy_gex_signals.decision_log import get_decision_logger
from spy_gex_signals.gex.validate import build_validation_report
from spy_gex_signals.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY",
                        help="Instrument name from config (SPY, GOLD) or a raw chain ticker")
    parser.add_argument("--file", help="Archived snapshot file instead of a live fetch")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(cfg.resolve_path(cfg.logging.dir))
    dlog = get_decision_logger(cfg.resolve_path(cfg.logging.decision_log))

    if args.file:
        snap = FileChainProvider(args.file).fetch()
    else:
        inst = cfg.instruments.get(args.symbol.upper())
        chain_symbol = inst.chain_symbol if inst else args.symbol.upper()
        snap = make_chain_provider(cfg.chain.provider).fetch(chain_symbol)
        ChainArchiver(cfg.resolve_path(cfg.chain.archive_dir)).save(snap)

    report = build_validation_report(snap, cfg.gex, decision_logger=dlog)

    reports_dir = cfg.resolve_path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = reports_dir / f"gex_validation_{snap.symbol}_{stamp}.md"
    out.write_text(report)

    print(report)
    print(f"\n[report written to {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
