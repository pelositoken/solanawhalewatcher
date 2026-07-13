"""Data models for the structure/liquidity engine.

Glossary mapping (framework spec → code):
    DOL  = draw on liquidity, the HTF external level price is drawn toward
    MSU  = market structure unit, the active LTF swing sequence
    MSS  = market structure shift, a break of that sequence (NEVER an entry)
    CSD  = change in state of delivery, the only valid entry trigger
    SMT  = smart-money divergence vs a correlated instrument (booster only)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class SwingKind(str, Enum):
    HIGH = "high"
    LOW = "low"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class Bar:
    index: int
    ts: datetime          # bar OPEN time, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)

    @property
    def range_mid(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def body_mid(self) -> float:
        return (self.open + self.close) / 2.0


@dataclass
class Swing:
    kind: SwingKind
    price: float
    bar_index: int        # bar where the extreme printed
    ts: datetime
    confirmed_at_index: int   # bar_index + k; the swing is UNUSABLE before this
    swept: bool = False   # price has traded beyond it since confirmation


@dataclass
class SweepEvent:
    """The inducement swing was actually traded beyond (wick suffices for the
    sweep itself — acceptance is CSD's job, not the sweep's)."""
    direction: Direction              # trade direction the sweep sets up
    inducement: Swing                 # the swing whose liquidity was run
    sweep_bar_index: int              # bar that (most recently) made the extreme
    sweep_extreme: float              # deepest price beyond the inducement so far
    first_sweep_index: int


@dataclass
class CsdEvent:
    rule_fired: str                   # '50pct' | 'prior_candle' | '50pct+prior_candle'
    bar_index: int
    close: float
    threshold_50pct: float | None
    threshold_prior: float | None            # effective (clamped ≥ the 50% midpoint)
    threshold_prior_boundary: float | None = None  # raw sweep-candle body/full boundary


@dataclass
class Fvg:
    """Three-candle imbalance (bar1 wick vs bar3 wick, gap left by bar2)."""
    direction: Direction
    upper: float
    lower: float
    created_index: int


@dataclass
class StructureSignal:
    instrument: str
    tf_pair: str                      # e.g. "1d/1h"
    direction: Direction
    created_ts: datetime
    entry_type: str                   # 'immediate' | 'fvg_retest'
    entry: float | None               # None while pending (awaiting next open / FVG touch)
    stop: float
    target_3r: float | None           # None until entry is known
    inducement_level: float
    sweep_extreme: float
    csd_rule_fired: str
    dol_level: float | None
    dol_r_multiple: float | None      # (DOL - entry) / risk, thesis stretch beyond 3R
    smt_status: str                   # 'divergence' | 'no_divergence' | 'disabled_low_correlation' | 'not_available'
    high_visibility: bool
    subtf_confirmation: str           # 'not_evaluated' (config off) | 'confirmed' | ...
    gex_alignment: str = "not_wired"  # Phase 4 fills this
    status: str = "pending"           # 'pending' | 'filled' | 'expired_unfilled'
    filled_bar_index: int | None = None   # LTF bar ordinal where the entry filled
    reasons: list[str] = field(default_factory=list)

    def finalize_entry(self, entry: float) -> None:
        """Set the fill price and derive the 3R management target.

        3R is management; DOL is thesis — if 3R lands short of the DOL that is
        by design (framework spec, 'Risk management: 3R fix').
        """
        self.entry = entry
        risk = abs(entry - self.stop)
        if risk <= 0:
            raise ValueError("entry equals stop; cannot size a 3R target")
        if self.direction == Direction.LONG:
            self.target_3r = entry + 3 * risk
        else:
            self.target_3r = entry - 3 * risk
        if self.dol_level is not None:
            signed = (self.dol_level - entry) if self.direction == Direction.LONG else (entry - self.dol_level)
            self.dol_r_multiple = signed / risk
        self.status = "filled"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["created_ts"] = self.created_ts.isoformat()
        return d
