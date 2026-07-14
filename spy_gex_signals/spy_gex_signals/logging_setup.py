"""Human-readable application logging (the JSONL decision log is separate,
see decision_log.py)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_dir: str | Path = "logs", level: int = logging.INFO) -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. under pytest)
        return
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(stream)

    file_handler = logging.FileHandler(log_dir / "app.log")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)
