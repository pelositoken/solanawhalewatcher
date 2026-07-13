"""Chain snapshot archiver.

The free CBOE source has no history, so every snapshot we ever fetch is
persisted immediately — this is what makes any future GEX backtest over the
live-collected period possible. Layout:

    {archive_dir}/{SYMBOL}/{YYYY-MM-DD}/{HHMMSS}Z.json.gz
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

from .models import ChainSnapshot

log = logging.getLogger(__name__)


class ChainArchiver:
    def __init__(self, archive_dir: str | Path):
        self.archive_dir = Path(archive_dir)

    def save(self, snap: ChainSnapshot) -> Path:
        day_dir = self.archive_dir / snap.symbol.upper() / snap.timestamp.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{snap.timestamp.strftime('%H%M%S')}Z.json.gz"
        with gzip.open(path, "wt") as f:
            json.dump(snap.to_dict(), f, separators=(",", ":"))
        log.info("Archived %s snapshot (%d quotes) → %s", snap.symbol, len(snap.quotes), path)
        return path

    def load(self, path: str | Path) -> ChainSnapshot:
        path = Path(path)
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt") as f:
            return ChainSnapshot.from_dict(json.load(f))

    def list_snapshots(self, symbol: str) -> list[Path]:
        sym_dir = self.archive_dir / symbol.upper()
        if not sym_dir.exists():
            return []
        return sorted(sym_dir.glob("*/*.json.gz"))
