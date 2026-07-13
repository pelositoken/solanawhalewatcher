"""The 4-step structure engine (framework spec):

1. HTF: bias + DOL (bias.py) — GEX regime gate plugs in here in Phase 4.
2. LTF: MSU tracking, inducement candidate = the most recent confirmed LTF
   swing low in a bullish bias (high in bearish) — "every swing low in a
   bullish trend is inducement".
3. CSD entry: inducement actually SWEPT (not approached), then body-close
   confirmation (csd.py), optional SMT booster, entry type immediate or
   FVG retest.
4. Signal carries stop (just beyond sweep extreme), mechanical 3R target,
   and the DOL as directional thesis.

Hard guardrails encoded here:
- There is NO code path from an MSS to a signal — MSS events are tracked as
  annotations only (double-MSU trap).
- CSD checks use closes only; a wick can never trigger anything.
- Every sweep considered, every rejection, and every signal goes to the
  decision log with the data it was based on.

The engine is a bar-by-bar state machine over COMPLETED bars, so running it
over history is mechanically identical to running it live (no hindsight).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

import pandas as pd

from ..config import StructureConfig
from ..data.price_provider import INTERVAL_DELTA
from ..decision_log import DecisionLogger
from .bias import HtfContext
from .csd import check_csd
from .models import Bar, Bias, Direction, Fvg, StructureSignal, SweepEvent, SwingKind
from .pd_arrays import detect_fvg
from .smt import SmtChecker, frame_to_bars
from .swings import MarketStructure

log = logging.getLogger(__name__)


@dataclass
class _ActiveSweep:
    sweep: SweepEvent
    sweep_bar: Bar
    bars_since_extreme: int = 0


@dataclass
class _PendingFvgEntry:
    signal: StructureSignal
    fvg: Fvg
    entry_price: float
    created_index: int


@dataclass
class EngineResult:
    signals: list[StructureSignal] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    mss_event_count: int = 0

    def bump(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1


class StructureEngine:
    def __init__(
        self,
        instrument: str,
        htf: str,
        ltf: str,
        cfg: StructureConfig,
        decision_logger: DecisionLogger | None = None,
        smt_checker: SmtChecker | None = None,
    ):
        self.instrument = instrument
        self.htf_tf = htf
        self.ltf_tf = ltf
        self.tf_pair = f"{htf}/{ltf}"
        self.cfg = cfg
        self.dlog = decision_logger
        self.smt = smt_checker

        self.htf_ctx = HtfContext(cfg)
        self.ltf_ms = MarketStructure(cfg.swing_strength)
        self._recent_bars: deque[Bar] = deque(maxlen=3)  # FVG window at the CSD bar
        self._tr: deque[float] = deque(maxlen=cfg.atr_period)
        self._prev_close: float | None = None
        self._active: _ActiveSweep | None = None
        self._pending_immediate: StructureSignal | None = None
        self._pending_fvgs: list[_PendingFvgEntry] = []
        self._consumed_swings: set[tuple[int, str]] = set()
        self._last_logged_bias: Bias | None = None
        self.result = EngineResult()

    # ---------- logging helper ----------

    def _decide(self, rule: str, inputs: dict, output, reason: str) -> None:
        if self.dlog is None:
            return
        inputs = {"instrument": self.instrument, "tf_pair": self.tf_pair, **inputs}
        self.dlog.log(component="structure.engine", rule=rule,
                      inputs=inputs, output=output, reason=reason)

    # ---------- main entry point ----------

    def run(self, htf_df: pd.DataFrame, ltf_df: pd.DataFrame) -> EngineResult:
        htf_bars = frame_to_bars(htf_df)
        ltf_bars = frame_to_bars(ltf_df)
        htf_delta = INTERVAL_DELTA[self.htf_tf]
        ltf_delta = INTERVAL_DELTA[self.ltf_tf]

        h = 0
        prev_bar: Bar | None = None
        for bar in ltf_bars:
            bar_close_ts = bar.ts + ltf_delta
            # Feed HTF bars that have COMPLETED by this LTF bar's close.
            while h < len(htf_bars) and htf_bars[h].ts + htf_delta <= bar_close_ts:
                self.htf_ctx.on_htf_bar(htf_bars[h])
                h += 1
            self._maybe_log_bias_change(bar)
            self.htf_ctx.mark_sweeps(bar)

            self._fill_pending_immediate(bar)
            self._process_pending_fvgs(bar)
            self.ltf_ms.update(bar)
            self._recent_bars.append(bar)
            self._update_atr(bar)

            if self._active is not None:
                self._process_active_sweep(bar, prev_bar)
            else:
                self._look_for_sweep(bar)
            prev_bar = bar

        self.result.mss_event_count = len(self.ltf_ms.mss_events)
        return self.result

    # ---------- pieces ----------

    def _maybe_log_bias_change(self, bar: Bar) -> None:
        if self.htf_ctx.bias != self._last_logged_bias:
            self._decide(
                rule="htf_bias_changed",
                inputs={"ltf_bar_ts": bar.ts.isoformat(),
                        "htf_swings": [(s.kind.value, s.price) for s in
                                       self.htf_ctx.structure.swings[-4:]]},
                output=self.htf_ctx.bias.value,
                reason="Bias = direction of most recent confirmed HTF swing sequence "
                       "(HH+HL bullish, LL+LH bearish, else neutral).",
            )
            self._last_logged_bias = self.htf_ctx.bias
            if self._active is not None:
                self._reject_active(bar, "bias_flipped_mid_setup")

    def _update_atr(self, bar: Bar) -> None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low, abs(bar.high - self._prev_close),
                     abs(bar.low - self._prev_close))
        self._tr.append(tr)
        self._prev_close = bar.close

    def _atr(self) -> float | None:
        if len(self._tr) < self._tr.maxlen:
            return None
        return sum(self._tr) / len(self._tr)

    def _inducement_candidate(self, bar: Bar):
        """Most recent confirmed, unconsumed LTF swing against the bias —
        the swing whose stops the model expects to be run before the real move."""
        bias = self.htf_ctx.bias
        kind = SwingKind.LOW if bias == Bias.BULLISH else SwingKind.HIGH
        for s in reversed(self.ltf_ms.swings):
            if s.kind != kind:
                continue
            if (s.bar_index, s.kind.value) in self._consumed_swings:
                continue
            if bar.index < s.confirmed_at_index:
                continue  # not yet knowable — no lookahead
            return s
        return None

    def _look_for_sweep(self, bar: Bar) -> None:
        bias = self.htf_ctx.bias
        if bias == Bias.NEUTRAL:
            self.result.bump("bars_neutral_bias")
            return
        candidate = self._inducement_candidate(bar)
        if candidate is None:
            self.result.bump("bars_no_inducement_candidate")
            return

        direction = Direction.LONG if bias == Bias.BULLISH else Direction.SHORT
        swept = (bar.low < candidate.price if direction == Direction.LONG
                 else bar.high > candidate.price)
        if not swept:
            return

        self.result.bump("sweeps_detected")
        dol = self.htf_ctx.current_dol(bar.close)
        if dol is None:
            self.result.bump("rejected_no_dol")
            self._consumed_swings.add((candidate.bar_index, candidate.kind.value))
            self._decide(
                rule="setup_rejected_no_dol",
                inputs={"bar_ts": bar.ts.isoformat(), "bias": bias.value,
                        "inducement_level": candidate.price},
                output="rejected",
                reason="Inducement swept but no unswept HTF external level exists in "
                       "the bias direction — no DOL means no directional thesis.",
            )
            return

        extreme = bar.low if direction == Direction.LONG else bar.high
        self._active = _ActiveSweep(
            sweep=SweepEvent(direction=direction, inducement=candidate,
                             sweep_bar_index=bar.index, sweep_extreme=extreme,
                             first_sweep_index=bar.index),
            sweep_bar=bar,
        )
        self._decide(
            rule="inducement_swept",
            inputs={"bar_ts": bar.ts.isoformat(), "bias": bias.value,
                    "inducement_level": candidate.price, "sweep_extreme": extreme,
                    "dol_level": dol.price},
            output={"direction": direction.value, "awaiting": "CSD body-close confirmation"},
            reason="Price traded beyond the inducement swing (wick suffices for the "
                   "sweep). Entry requires CSD — an MSS or the sweep alone never "
                   "triggers (double-MSU trap guardrail).",
        )

    def _process_active_sweep(self, bar: Bar, prev_bar: Bar | None) -> None:
        a = self._active
        d = a.sweep.direction

        # A deeper grab restarts the confirmation window on the new sweep candle.
        made_new_extreme = (bar.low < a.sweep.sweep_extreme if d == Direction.LONG
                            else bar.high > a.sweep.sweep_extreme)
        if made_new_extreme:
            a.sweep.sweep_extreme = bar.low if d == Direction.LONG else bar.high
            a.sweep.sweep_bar_index = bar.index
            a.sweep_bar = bar
            a.bars_since_extreme = 0
            return  # CSD is judged on candles AFTER the sweep candle

        a.bars_since_extreme += 1
        if prev_bar is None:
            return

        csd = check_csd(bar, prev_bar, a.sweep, a.sweep_bar, self.cfg)
        if csd is not None:
            self._emit_signal(bar, csd)
            return

        if a.bars_since_extreme > self.cfg.max_bars_sweep_to_csd:
            self._reject_active(bar, "expired_no_csd")

    def _reject_active(self, bar: Bar, why: str) -> None:
        a = self._active
        self.result.bump(f"rejected_{why}")
        self._consumed_swings.add((a.sweep.inducement.bar_index, a.sweep.inducement.kind.value))
        self._decide(
            rule=f"setup_{why}",
            inputs={"bar_ts": bar.ts.isoformat(),
                    "inducement_level": a.sweep.inducement.price,
                    "sweep_extreme": a.sweep.sweep_extreme,
                    "bars_waited": a.bars_since_extreme,
                    "max_bars": self.cfg.max_bars_sweep_to_csd},
            output="rejected",
            reason=("No body-close CSD within the allowed window — wicks reached "
                    "levels but the market never accepted trading beyond them."
                    if why == "expired_no_csd" else
                    "HTF bias flipped while awaiting confirmation; setup void."),
        )
        self._active = None

    def _emit_signal(self, bar: Bar, csd) -> None:
        a = self._active
        d = a.sweep.direction
        spot = bar.close

        buffer = a.sweep.sweep_extreme * self.cfg.invalidation_buffer_pct
        stop = (a.sweep.sweep_extreme - buffer if d == Direction.LONG
                else a.sweep.sweep_extreme + buffer)

        dol = self.htf_ctx.current_dol(spot)
        atr = self._atr()
        displacement = abs(csd.close - a.sweep.sweep_extreme)
        high_vis = bool(atr is not None
                        and displacement > self.cfg.high_visibility_atr_mult * atr)

        smt_status, smt_detail = ("not_available", {})
        if self.smt is not None:
            smt_status, smt_detail = self.smt.check(bar.ts, d)
        self.result.bump(f"smt_{smt_status}")

        if self.cfg.require_subtf_confirmation:
            # Sub-LTF data isn't plumbed in Phase 2; refusing silently would hide
            # a config the user set, so reject loudly instead.
            self.result.bump("rejected_subtf_required_but_unavailable")
            self._decide(
                rule="setup_rejected_subtf_unavailable",
                inputs={"bar_ts": bar.ts.isoformat()},
                output="rejected",
                reason="require_subtf_confirmation is enabled but no sub-timeframe "
                       "data is wired in Phase 2. Disable the flag or wait for the "
                       "sub-TF plumbing.",
            )
            self._consumed_swings.add((a.sweep.inducement.bar_index, a.sweep.inducement.kind.value))
            self._active = None
            return
        subtf = "not_evaluated"

        signal = StructureSignal(
            instrument=self.instrument,
            tf_pair=self.tf_pair,
            direction=d,
            created_ts=bar.ts,
            entry_type=self.cfg.entry_type,
            entry=None,
            stop=stop,
            target_3r=None,
            inducement_level=a.sweep.inducement.price,
            sweep_extreme=a.sweep.sweep_extreme,
            csd_rule_fired=csd.rule_fired,
            dol_level=dol.price if dol else None,
            dol_r_multiple=None,
            smt_status=smt_status,
            high_visibility=high_vis,
            subtf_confirmation=subtf,
            gex_alignment=self.htf_ctx.check_dol_gex_alignment(dol),
            reasons=[f"csd:{csd.rule_fired}", f"bias:{self.htf_ctx.bias.value}"],
        )

        self._decide(
            rule="csd_confirmed",
            inputs={"bar_ts": bar.ts.isoformat(),
                    "csd_close": csd.close,
                    "threshold_50pct": csd.threshold_50pct,
                    "threshold_prior_candle": csd.threshold_prior,
                    "sweep_extreme": a.sweep.sweep_extreme,
                    "inducement_level": a.sweep.inducement.price,
                    "dol_level": signal.dol_level,
                    "atr": atr, "displacement": displacement,
                    "smt": {"status": smt_status, **smt_detail}},
            output={"direction": d.value, "rule_fired": csd.rule_fired,
                    "stop": stop, "entry_type": self.cfg.entry_type,
                    "high_visibility": high_vis,
                    "gex_alignment": signal.gex_alignment},
            reason=f"Body close satisfied CSD rule(s) [{csd.rule_fired}] — wicks alone "
                   f"never trigger. SMT is a confidence flag, not a gate. GEX alignment "
                   f"not wired until Phase 4.",
        )

        self._consumed_swings.add((a.sweep.inducement.bar_index, a.sweep.inducement.kind.value))
        self._active = None

        if self.cfg.entry_type == "immediate":
            self._pending_immediate = signal
        else:
            self._queue_fvg_entry(signal, bar)

    def _fill_pending_immediate(self, bar: Bar) -> None:
        if self._pending_immediate is None:
            return
        signal = self._pending_immediate
        self._pending_immediate = None
        signal.finalize_entry(bar.open)
        self.result.signals.append(signal)
        self.result.bump("signals_emitted")
        self._decide(
            rule="signal_filled_immediate",
            inputs={"bar_ts": bar.ts.isoformat()},
            output=signal.to_dict(),
            reason="Entry 1: fill at the open of the candle immediately following "
                   "CSD confirmation (wider stop, higher fill probability).",
        )

    def _queue_fvg_entry(self, signal: StructureSignal, csd_bar: Bar) -> None:
        # FVG left by the confirmation move: the 3-candle window ending at the CSD bar.
        window = list(self._recent_bars)
        fvg = detect_fvg(*window) if len(window) == 3 else None
        if fvg is None or fvg.direction != signal.direction:
            self.result.bump("rejected_no_fvg_for_retest")
            self._decide(
                rule="setup_rejected_no_fvg",
                inputs={"csd_bar_ts": csd_bar.ts.isoformat()},
                output="rejected",
                reason="entry_type=fvg_retest but the confirmation move left no "
                       "matching FVG — per spec the trade runs without us.",
            )
            return
        entry_price = fvg.upper if signal.direction == Direction.LONG else fvg.lower
        self._pending_fvgs.append(_PendingFvgEntry(signal=signal, fvg=fvg,
                                                   entry_price=entry_price,
                                                   created_index=csd_bar.index))
        self._decide(
            rule="fvg_entry_queued",
            inputs={"csd_bar_ts": csd_bar.ts.isoformat(),
                    "fvg_upper": fvg.upper, "fvg_lower": fvg.lower},
            output={"entry_price": entry_price,
                    "expires_after_bars": self.cfg.fvg_retest_expiry_bars},
            reason="Entry 2: limit at the confirmation move's FVG (tighter risk, "
                   "trade may run unfilled).",
        )

    def _process_pending_fvgs(self, bar: Bar) -> None:
        still: list[_PendingFvgEntry] = []
        for p in self._pending_fvgs:
            touched = (bar.low <= p.entry_price if p.signal.direction == Direction.LONG
                       else bar.high >= p.entry_price)
            if touched:
                # Gap-through protection: never assume a better fill than the open.
                if p.signal.direction == Direction.LONG:
                    fill = min(bar.open, p.entry_price)
                else:
                    fill = max(bar.open, p.entry_price)
                p.signal.finalize_entry(fill)
                self.result.signals.append(p.signal)
                self.result.bump("signals_emitted")
                self._decide(
                    rule="signal_filled_fvg_retest",
                    inputs={"bar_ts": bar.ts.isoformat(), "fvg_entry": p.entry_price},
                    output=p.signal.to_dict(),
                    reason="Price retraced into the confirmation FVG.",
                )
            elif bar.index - p.created_index > self.cfg.fvg_retest_expiry_bars:
                p.signal.status = "expired_unfilled"
                self.result.bump("fvg_entries_expired_unfilled")
                self._decide(
                    rule="fvg_entry_expired",
                    inputs={"bar_ts": bar.ts.isoformat(), "fvg_entry": p.entry_price,
                            "bars_waited": bar.index - p.created_index},
                    output="expired_unfilled",
                    reason="FVG never revisited within the expiry window — accepted "
                           "cost of Entry 2 (fill probability vs entry quality).",
                )
            else:
                still.append(p)
        self._pending_fvgs = still
