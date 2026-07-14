"""Phase 3: structure-only backtest — NO GEX gate, NO sizing.

Answers one question: does the price-action logic alone show any edge,
before the GEX layer earns its complexity?

Mechanics (fixed-bracket, per the framework's '3R fix'):
- Signals come from the Phase 2 engine run bar-by-bar over history —
  the identical code path a live run uses, so there is no hindsight.
- Entry-1 (next-candle open) only; every report is labeled as such.
- Exit at stop (−1R) or at the 3R target (+3R). Nothing in between.
- Same-bar ambiguity: if one bar touches BOTH stop and target, the trade
  is scored as a LOSS (conservative) and counted in `ambiguous` so the
  report shows how often the bracket couldn't be resolved at bar
  resolution.
- Trades still open at end of data are excluded from closed-trade metrics
  and reported separately.
- Trades are simulated independently — no concurrency cap, no sizing;
  those belong to Phase 5 and would only reduce exposure, not create edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from ..config import StructureConfig
from ..decision_log import DecisionLogger
from ..structure.engine import StructureEngine
from ..structure.models import Direction, StructureSignal
from ..structure.smt import SmtChecker

WIN_R = 3.0
LOSS_R = -1.0
BREAKEVEN_WIN_RATE = 0.25  # for a fixed −1/+3 bracket


@dataclass
class TradeResult:
    signal: StructureSignal
    outcome: str            # 'win' | 'loss' | 'open'
    r_multiple: float | None
    exit_index: int | None
    bars_held: int | None
    ambiguous: bool = False


@dataclass
class PairMetrics:
    label: str
    max_bars_sweep_to_csd: int
    n_signals: int
    n_closed: int
    n_open: int
    n_ambiguous: int
    n_excluded_roll: int
    wins: int
    losses: int
    win_rate: float | None
    avg_r: float | None
    total_r: float | None
    max_drawdown_r: float | None
    trades_per_month: float | None
    span_days: float
    counters: dict = field(default_factory=dict)
    trades: list[TradeResult] = field(default_factory=list)


def simulate_trade(signal: StructureSignal, ltf_df: pd.DataFrame) -> TradeResult:
    """Walk bars from the fill bar; stop touch loses first on ambiguity."""
    i0 = signal.filled_bar_index
    highs = ltf_df["high"].to_numpy()
    lows = ltf_df["low"].to_numpy()
    long = signal.direction == Direction.LONG

    for i in range(i0, len(ltf_df)):
        if long:
            stop_hit = lows[i] <= signal.stop
            target_hit = highs[i] >= signal.target_3r
        else:
            stop_hit = highs[i] >= signal.stop
            target_hit = lows[i] <= signal.target_3r
        if stop_hit and target_hit:
            return TradeResult(signal, "loss", LOSS_R, i, i - i0, ambiguous=True)
        if stop_hit:
            return TradeResult(signal, "loss", LOSS_R, i, i - i0)
        if target_hit:
            return TradeResult(signal, "win", WIN_R, i, i - i0)
    return TradeResult(signal, "open", None, None, None)


def spans_roll(trade: TradeResult, ltf_index, roll_dates) -> bool:
    """True if the trade's full window — first inducement sweep through exit
    (or end of data while open) — contains a continuous-contract roll. Such
    trades are built on splice artifacts and must not count as evidence."""
    if not roll_dates:
        return False
    start = trade.signal.first_sweep_ts
    end = (ltf_index[trade.exit_index] if trade.exit_index is not None
           else ltf_index[-1])
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    return any(start <= pd.Timestamp(r) <= end for r in roll_dates)


def compute_metrics(label: str, max_bars: int, trades: list[TradeResult],
                    counters: dict, span_days: float,
                    n_excluded_roll: int = 0) -> PairMetrics:
    closed = [t for t in trades if t.outcome != "open"]
    wins = sum(1 for t in closed if t.outcome == "win")
    losses = len(closed) - wins

    # Equity curve in R, booked at exit, for max drawdown.
    max_dd = None
    if closed:
        equity = peak = dd = 0.0
        for t in sorted(closed, key=lambda t: t.exit_index):
            equity += t.r_multiple
            peak = max(peak, equity)
            dd = max(dd, peak - equity)
        max_dd = dd

    n_closed = len(closed)
    return PairMetrics(
        label=label,
        max_bars_sweep_to_csd=max_bars,
        n_signals=len(trades),
        n_closed=n_closed,
        n_open=len(trades) - n_closed,
        n_ambiguous=sum(1 for t in closed if t.ambiguous),
        n_excluded_roll=n_excluded_roll,
        wins=wins,
        losses=losses,
        win_rate=wins / n_closed if n_closed else None,
        avg_r=sum(t.r_multiple for t in closed) / n_closed if n_closed else None,
        total_r=sum(t.r_multiple for t in closed) if n_closed else None,
        max_drawdown_r=max_dd,
        trades_per_month=len(trades) / span_days * 30.44 if span_days > 0 else None,
        span_days=span_days,
        counters=counters,
        trades=trades,
    )


def run_pair_backtest(
    instrument: str,
    htf: str,
    ltf: str,
    cfg: StructureConfig,
    htf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    smt_checker_factory=None,
    max_bars_values: tuple[int, ...] = (3, 5, 8),
    decision_logger: DecisionLogger | None = None,
    roll_dates: list | None = None,
) -> list[PairMetrics]:
    """Run the engine + simulator at each max_bars_sweep_to_csd sensitivity value.

    Entry-1 only is enforced here regardless of config (Phase 3 scope);
    smt_checker_factory is called per run because SmtChecker is stateful.
    roll_dates: continuous-futures roll timestamps for THIS instrument —
    trades whose window spans one are excluded and counted, never scored.
    """
    span_days = ((ltf_df.index[-1] - ltf_df.index[0]).total_seconds() / 86400
                 if len(ltf_df) > 1 else 0.0)
    out: list[PairMetrics] = []
    for mb in max_bars_values:
        run_cfg = replace(cfg, max_bars_sweep_to_csd=mb, entry_type="immediate")
        smt = smt_checker_factory() if smt_checker_factory else None
        engine = StructureEngine(instrument, htf, ltf, run_cfg,
                                 decision_logger=decision_logger, smt_checker=smt)
        result = engine.run(htf_df, ltf_df)
        all_trades = [simulate_trade(s, ltf_df) for s in result.signals
                      if s.status == "filled"]
        trades = [t for t in all_trades
                  if not spans_roll(t, ltf_df.index, roll_dates)]
        out.append(compute_metrics(f"{instrument} {htf}/{ltf}", mb, trades,
                                   result.counters, span_days,
                                   n_excluded_roll=len(all_trades) - len(trades)))
    return out


def _fmt(v, pct=False, nd=2):
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if pct else f"{v:.{nd}f}"


def render_report(all_metrics: dict[str, list[PairMetrics]],
                  default_max_bars: int, data_notes: list[str]) -> str:
    """Markdown report: per-pair metrics (never combined), sensitivity table,
    and the explicit edge-checkpoint verdict."""
    lines = [
        "# Phase 3 — Structure-only backtest (NO GEX gate)",
        "",
        "**Scope labels — read before the numbers:**",
        "",
        "- **Entry-1 (next-candle open) only.** FVG-retest entries are excluded.",
        "- Fixed bracket: −1R stop / +3R target; breakeven win rate is 25%.",
        "- Same-bar stop+target ambiguity is scored as a LOSS (conservative);",
        "  the `ambig` column shows how often that happened.",
        "- No GEX, no sizing, no concurrency cap — structure logic in isolation.",
        "- Each timeframe pair is reported separately and must be judged on its",
        "  own sample size and history depth.",
        "",
    ]
    for note in data_notes:
        lines.append(f"- {note}")
    lines += ["", "## Results by instrument / timeframe pair", ""]

    header = ("| pair | max_bars | signals | closed | open | wins | losses | ambig "
              "| excl_roll | win rate | avg R | total R | max DD (R) | trades/mo |")
    sep = "|" + "---|" * 13

    for label, metrics_list in all_metrics.items():
        m0 = metrics_list[0]
        lines += [f"### {label}",
                  "",
                  f"- LTF span: {m0.span_days:.0f} days",
                  "",
                  header, sep]
        for m in metrics_list:
            star = " **(default)**" if m.max_bars_sweep_to_csd == default_max_bars else ""
            lines.append(
                f"| {m.label} | {m.max_bars_sweep_to_csd}{star} | {m.n_signals} "
                f"| {m.n_closed} | {m.n_open} | {m.wins} | {m.losses} "
                f"| {m.n_ambiguous} | {m.n_excluded_roll} | {_fmt(m.win_rate, pct=True)} "
                f"| {_fmt(m.avg_r)} | {_fmt(m.total_r)} "
                f"| {_fmt(m.max_drawdown_r)} | {_fmt(m.trades_per_month, nd=1)} |")
        lines.append("")

    lines += ["## Edge checkpoint", ""]
    for label, metrics_list in all_metrics.items():
        m = next((x for x in metrics_list
                  if x.max_bars_sweep_to_csd == default_max_bars), metrics_list[0])
        lines.append(f"- **{label}** (at default max_bars={m.max_bars_sweep_to_csd}): "
                     + edge_verdict(m))
    lines += [
        "",
        "**Checkpoint rule (from the project brief):** if the structure-only",
        "baseline shows no real edge, building the GEX layer on top wastes effort",
        "and produces a system that looks sophisticated but isn't. Positive avg R",
        "on a thin sample is NOT confirmation of edge — treat n < 30 closed trades",
        "as insufficient regardless of the numbers.",
    ]
    return "\n".join(lines)


def edge_verdict(m: PairMetrics) -> str:
    if m.n_closed == 0:
        return "NO TRADES — nothing to evaluate on this pair/history."
    if m.n_closed < 30:
        return (f"INSUFFICIENT SAMPLE ({m.n_closed} closed trades; win rate "
                f"{_fmt(m.win_rate, pct=True)}, avg R {_fmt(m.avg_r)}). "
                f"Do not conclude edge or no-edge from this.")
    if m.avg_r is not None and m.avg_r > 0:
        return (f"POSSIBLE EDGE: {m.n_closed} closed trades, win rate "
                f"{_fmt(m.win_rate, pct=True)} (breakeven 25%), avg R {_fmt(m.avg_r)}, "
                f"max DD {_fmt(m.max_drawdown_r)}R. Needs Phase 6 walk-forward before "
                f"any weight is put on it.")
    return (f"NO EDGE on this pair: {m.n_closed} closed trades, win rate "
            f"{_fmt(m.win_rate, pct=True)} vs 25% breakeven, avg R {_fmt(m.avg_r)}. "
            f"Per the checkpoint rule, do NOT proceed to the GEX layer expecting "
            f"it to rescue this.")
