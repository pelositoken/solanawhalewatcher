#!/usr/bin/env python3
"""Fetch the current options chain, archive it, and print a GEX summary.

Usage:
    python scripts/fetch_snapshot.py --symbol SPY
    python scripts/fetch_snapshot.py --symbol GOLD    # uses GLD chain proxy
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spy_gex_signals.config import load_config
from spy_gex_signals.data.archiver import ChainArchiver
from spy_gex_signals.data.chain_provider import make_chain_provider
from spy_gex_signals.decision_log import get_decision_logger
from spy_gex_signals.gex.engine import compute_gex
from spy_gex_signals.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY",
                        help="Instrument name from config (SPY, GOLD) or a raw chain ticker")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(cfg.resolve_path(cfg.logging.dir))
    dlog = get_decision_logger(cfg.resolve_path(cfg.logging.decision_log))

    inst = cfg.instruments.get(args.symbol.upper())
    chain_symbol = inst.chain_symbol if inst else args.symbol.upper()

    provider = make_chain_provider(cfg.chain.provider)
    snap = provider.fetch(chain_symbol)

    archiver = ChainArchiver(cfg.resolve_path(cfg.chain.archive_dir))
    path = archiver.save(snap)

    result = compute_gex(snap, cfg.gex, decision_logger=dlog)

    flip = f"{result.flip_level:,.2f}" if result.flip_level is not None else "n/a"
    print()
    print(f"=== {snap.symbol} chain snapshot @ {snap.timestamp.isoformat()} ===")
    print(f"Spot (delayed):      {snap.underlying_price:,.2f}")
    print(f"Quotes:              {result.n_quotes_used} used / {result.n_quotes_total} total")
    print(f"Net GEX:             ${result.net_gex_bn_per_pct:,.3f} Bn per 1% move "
          f"({'positive' if result.net_gex_per_pct > 0 else 'negative'} gamma)")
    print(f"Zero-gamma flip:     {flip}")
    print(f"Sign convention:     {result.sign_convention}")
    print(f"Archived to:         {path}")
    print(f"Decision log:        {dlog.path}")
    print()
    print("Top gamma strikes ($M net GEX per 1%):")
    for strike, row in result.top_strikes(cfg.gex.top_n_strikes).iterrows():
        print(f"  {strike:>8g}  {row['net_gex'] / 1e6:>12,.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
