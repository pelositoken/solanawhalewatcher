#!/usr/bin/env python3
"""Sanity-check the price layer: pull every configured instrument/timeframe
and report bar counts and date ranges, so the honest backtest depth per
timeframe is visible up front.

Usage:
    python scripts/check_price_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spy_gex_signals.config import load_config
from spy_gex_signals.data.price_provider import make_price_provider
from spy_gex_signals.logging_setup import setup_logging


def main() -> int:
    cfg = load_config()
    setup_logging(cfg.resolve_path(cfg.logging.dir))
    provider = make_price_provider(cfg.price.provider, cfg)

    print(f"Price provider: {provider.name}\n")
    print(f"{'instrument':<10} {'ticker':<8} {'tf':<5} {'bars':>7}  range")
    print("-" * 78)
    for name, inst in cfg.instruments.items():
        for tf in cfg.price.timeframes:
            try:
                df = provider.get_history(inst.price_symbol, tf)
                rng = (f"{df.index[0].date()} → {df.index[-1].date()}" if len(df) else "EMPTY")
                print(f"{name:<10} {inst.price_symbol:<8} {tf:<5} {len(df):>7}  {rng}")
            except Exception as e:  # keep going so one bad tf doesn't hide the rest
                print(f"{name:<10} {inst.price_symbol:<8} {tf:<5} {'ERROR':>7}  {e}")
    print("\nNote: 15m/5m depth is capped by yfinance (~60 days). Phase 3's structure-only")
    print("backtest will state which timeframes the data honestly supports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
