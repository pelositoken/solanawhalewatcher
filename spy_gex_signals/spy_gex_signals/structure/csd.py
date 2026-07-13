"""CSD (Change in State of Delivery) — the ONLY valid entry trigger.

Framework spec, verbatim constraints implemented here:
- The sweep itself may be a wick (a wick shows the level was reached).
- Confirmation is a BODY CLOSE only — wicks never trigger, anywhere.
- Rule '50pct': a candle's body closes past the halfway point of the sweep
  candle (midpoint of its range or body, configurable; default range).
- Rule 'prior_candle' (stronger): the candle's body closes beyond the
  entire SWEEP candle (its body or full range incl. wicks, configurable;
  default body). Per user review: "prior candle" always resolves to the
  sweep candle — the latest sweep-extreme candle under the deeper-grab
  reset logic — never to an arbitrary interim bar, so a slow multi-bar
  recovery can't confirm against a small predecessor body.
- Strictly-stronger guarantee: the prior_candle threshold is clamped to be
  at least as demanding as the 50% midpoint (max of the two for longs, min
  for shorts). Without the clamp, a sweep candle with a huge opposing wick
  could have a body boundary inside the 50% zone. Both components are
  recorded on the CsdEvent for the audit trail.
- 'both' = either rule fires; which one fired is recorded on the signal.
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


def sweep_candle_boundary(sweep_bar: Bar, direction: Direction, scope: str) -> float:
    """The sweep candle's far boundary the confirming close must clear."""
    if scope == "body":
        return sweep_bar.body_high if direction == Direction.LONG else sweep_bar.body_low
    if scope == "full":
        return sweep_bar.high if direction == Direction.LONG else sweep_bar.low
    raise ValueError(f"unknown prior-candle scope {scope!r}")


def check_csd(
    bar: Bar,
    sweep: SweepEvent,
    sweep_bar: Bar,
    cfg: StructureConfig,
) -> CsdEvent | None:
    """Evaluate CSD on a bar that CLOSED after the (latest) sweep candle.

    Uses bar.close exclusively — by construction a wick (bar.high/bar.low)
    can never satisfy these checks.
    """
    direction = sweep.direction
    fired: list[str] = []

    mid = sweep_candle_midpoint(sweep_bar, cfg.sweep_candle_midpoint)

    if cfg.csd_rule in ("50pct", "both"):
        ok = bar.close > mid if direction == Direction.LONG else bar.close < mid
        if ok:
            fired.append("50pct")

    prior_thr = None
    prior_boundary = None
    if cfg.csd_rule in ("prior_candle", "both"):
        prior_boundary = sweep_candle_boundary(sweep_bar, direction, cfg.prior_candle_scope)
        # Clamp so this rule is never weaker than the 50% rule.
        prior_thr = (max(prior_boundary, mid) if direction == Direction.LONG
                     else min(prior_boundary, mid))
        ok = bar.close > prior_thr if direction == Direction.LONG else bar.close < prior_thr
        if ok:
            fired.append("prior_candle")

    if not fired:
        return None
    return CsdEvent(
        rule_fired="+".join(fired),
        bar_index=bar.index,
        close=bar.close,
        threshold_50pct=mid if cfg.csd_rule in ("50pct", "both") else None,
        threshold_prior=prior_thr,
        threshold_prior_boundary=prior_boundary,
    )
