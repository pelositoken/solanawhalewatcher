"""Structured decision audit log.

Guardrail from the project brief: every function that makes a
trading-relevant decision must log its full reasoning — which rule fired
and what data it used — not just the final output. This module is the
single sink for that. Entries are append-only JSONL so they can be replayed
and audited after the fact.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    """Best-effort conversion so a decision entry can never fail to serialize."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        if hasattr(value, "isoformat"):  # date/datetime/time
            return value.isoformat()
        return repr(value)


class DecisionLogger:
    """Append-only JSONL decision log, safe for concurrent use in-process."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        component: str,
        rule: str,
        inputs: dict[str, Any],
        output: Any,
        reason: str,
    ) -> dict[str, Any]:
        """Record one decision.

        component: which module/function decided (e.g. "gex.engine.net_gex")
        rule:      identifier of the rule that fired (e.g. "sign_convention.standard")
        inputs:    the data the decision was based on
        output:    the decision/result
        reason:    human-readable explanation of why this output followed
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": component,
            "rule": rule,
            "inputs": _jsonable(inputs),
            "output": _jsonable(output),
            "reason": reason,
        }
        line = json.dumps(entry, separators=(",", ":"))
        with self._lock:
            with open(self.path, "a") as f:
                f.write(line + "\n")
        return entry


_default: DecisionLogger | None = None
_default_lock = threading.Lock()


def get_decision_logger(path: str | Path | None = None) -> DecisionLogger:
    """Return the process-wide decision logger (created on first use)."""
    global _default
    with _default_lock:
        if _default is None or (path is not None and Path(path) != _default.path):
            if path is None:
                from .config import load_config

                cfg = load_config()
                path = cfg.resolve_path(cfg.logging.decision_log)
            _default = DecisionLogger(path)
        return _default
