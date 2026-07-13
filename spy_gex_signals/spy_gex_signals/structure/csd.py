"""CSD (Change in State of Delivery) — the ONLY valid entry trigger.

Framework spec, verbatim constraints implemented here:
- The sweep itself may be a wick (a wick shows the level was reached).
- Confirmation is a BODY CLOSE only — wicks never trigger, anywhere.
- Rule '50pct': a candle's body closes past the halfway point of the sweep
  candle (midpoint of its range or body, configurable; default range).
- Rule 'prior_candle' (stronger): the candle's body closes beyond the
  entire prior candle (its body or full range incl. wicks, configurable;
  default body).
- 'both' = either rule fires; which one fired is recorded on the signal.

Interpretation note (flagged for user confirmation at the Phase 2 review):
'prior candle' is read literally as the candle immediately preceding the
CONFIRMING candle. In the canonical sweep→confirm sequence that IS the
sweep candle; when confirmation comes later, it is whatever candle came
just before the one that confirms.
"""

from __future__ import annotations

from ..config import StructureConfig
from .models import Bar, CsdEvent, Direction, SweepEvent


def sweep_candle_midpoint(sweep_bar: Bar, mode: str) -> float:
    if mode == "range":
        return sweep_bar.range_mid
    if mode == "body":
        return sweep_bar.body_mid
    raise ValueError(f"unknown midpoint mode {mode!r}")


def prior_candle_threshold(prior_bar: Bar, direction: Direction, scope: str) -> float:
    if scope == "body":
        return prior_bar.body_high if direction == Direction.LONG else prior_bar.body_low
    if scope == "full":
        return prior_bar.high if direction == Direction.LONG else prior_bar.low
    raise ValueError(f"unknown prior-candle scope {scope!r}")


def check_csd(
    bar: Bar,
    prior_bar: Bar,
    sweep: SweepEvent,
    sweep_bar: Bar,
    cfg: StructureConfig,
) -> CsdEvent | None:
    """Evaluate CSD on a bar that CLOSED after the sweep candle.

    Uses bar.close exclusively — by construction a wick (bar.high/bar.low)
    can never satisfy these checks.
    """
    direction = sweep.direction
    fired: list[str] = []

    mid = None
    if cfg.csd_rule in ("50pct", "both"):
        mid = sweep_candle_midpoint(sweep_bar, cfg.sweep_candle_midpoint)
        ok = bar.close > mid if direction == Direction.LONG else bar.close < mid
        if ok:
            fired.append("50pct")

    prior_thr = None
    if cfg.csd_rule in ("prior_candle", "both"):
        prior_thr = prior_candle_threshold(prior_bar, direction, cfg.prior_candle_scope)
        ok = bar.close > prior_thr if direction == Direction.LONG else bar.close < prior_thr
        if ok:
            fired.append("prior_candle")

    if not fired:
        return None
    return CsdEvent(
        rule_fired="+".join(fired),
        bar_index=bar.index,
        close=bar.close,
        threshold_50pct=mid,
        threshold_prior=prior_thr,
    )
