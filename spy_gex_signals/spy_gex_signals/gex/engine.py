"""Dealer gamma exposure (GEX) computation.

Methodology (see README for the full statement):

- Dealer positioning assumption is configurable:
    'standard' (default): dealers long calls / short puts — the
        SqueezeMetrics/SpotGamma convention. Call gamma counts positive
        dealer gamma, put gamma negative.
    'inverse': dealers short calls / long puts — flips every sign.
- Per-row GEX in dollars per 1% underlying move:
      gamma × OI × contract_multiplier × spot² × 0.01 × sign(type)
- Net GEX is the sum across the (filtered) chain; the per-strike profile is
  the same quantity grouped by strike.
- Zero-gamma flip level: net GEX is recomputed on a grid of hypothetical
  spot levels, re-evaluating each option's Black-Scholes gamma at that
  level with its implied vol held fixed (sticky-strike assumption), and the
  sign crossing nearest to current spot is linearly interpolated.

Every computation logs its reasoning to the decision log (audit guardrail).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import GexConfig
from ..data.models import ChainSnapshot
from ..decision_log import DecisionLogger
from .greeks import bs_gamma, bs_gamma_vec

log = logging.getLogger(__name__)


@dataclass
class GexResult:
    symbol: str
    spot: float
    timestamp: str
    sign_convention: str
    exclude_0dte: bool
    max_dte: int | None
    net_gex_per_pct: float            # dollars per 1% move (signed)
    flip_level: float | None          # zero-gamma spot level, None if no crossing in range
    per_strike: pd.DataFrame          # index strike; columns call_gex, put_gex, net_gex ($/1%)
    n_quotes_total: int
    n_quotes_used: int
    n_gamma_fallback: int             # rows priced with BSM because vendor gamma was missing
    n_flip_rows_dropped: int          # rows excluded from flip search (no usable IV)

    @property
    def net_gex_bn_per_pct(self) -> float:
        return self.net_gex_per_pct / 1e9

    def top_strikes(self, n: int = 5) -> pd.DataFrame:
        return self.per_strike.reindex(
            self.per_strike["net_gex"].abs().sort_values(ascending=False).index
        ).head(n)


def _sign_for(option_type: str, convention: str) -> int:
    base = 1 if option_type == "C" else -1  # standard: dealers long calls / short puts
    return base if convention == "standard" else -base


def _chain_frame(snap: ChainSnapshot, cfg: GexConfig, exclude_0dte: bool) -> tuple[pd.DataFrame, int]:
    """Filtered per-row frame with effective gamma. Returns (frame, n_fallback)."""
    snap_date = snap.snapshot_date
    rows = []
    n_fallback = 0
    for q in snap.quotes:
        if q.open_interest <= 0:
            continue
        dte = (q.expiration - snap_date).days
        if dte < 0:
            continue
        if exclude_0dte and dte == 0:
            continue
        if cfg.max_dte is not None and dte > cfg.max_dte:
            continue
        # Expiry treated as end of the expiration day → 0DTE keeps a small
        # positive time value instead of degenerating to zero.
        t_years = (dte + 0.5) / 365.0
        gamma = q.gamma
        if gamma is None or gamma == 0:
            gamma = bs_gamma(snap.underlying_price, q.strike, t_years, q.iv or 0.0,
                             r=cfg.risk_free_rate)
            if gamma > 0:
                n_fallback += 1
        rows.append({
            "option_type": q.option_type,
            "strike": q.strike,
            "dte": dte,
            "t_years": t_years,
            "oi": q.open_interest,
            "iv": q.iv if (q.iv or 0) > 0 else np.nan,
            "gamma": float(gamma or 0.0),
            "sign": _sign_for(q.option_type, cfg.dealer_sign_convention),
        })
    df = pd.DataFrame(rows)
    return df, n_fallback


def _flip_level(
    df: pd.DataFrame,
    spot: float,
    cfg: GexConfig,
) -> tuple[float | None, int]:
    """Zero-gamma flip via BSM re-pricing on a spot grid (sticky-strike IV).

    Returns (flip_level, n_rows_dropped_for_missing_iv).
    """
    usable = df.dropna(subset=["iv"])
    usable = usable[usable["iv"] > 0]
    n_dropped = len(df) - len(usable)
    if usable.empty:
        return None, n_dropped

    strikes = usable["strike"].to_numpy()
    t_years = usable["t_years"].to_numpy()
    ivs = usable["iv"].to_numpy()
    weights = (usable["sign"] * usable["oi"]).to_numpy() * cfg.contract_multiplier

    grid = np.linspace(spot * (1 - cfg.flip_search_pct),
                       spot * (1 + cfg.flip_search_pct),
                       cfg.flip_grid_points)
    net = np.empty_like(grid)
    for i, s in enumerate(grid):
        gammas = bs_gamma_vec(s, strikes, t_years, ivs, r=cfg.risk_free_rate)
        net[i] = float(np.sum(weights * gammas) * s * s * 0.01)

    # Sign crossings; pick the one nearest current spot.
    sign_change = np.where(np.diff(np.sign(net)) != 0)[0]
    if len(sign_change) == 0:
        return None, n_dropped
    candidates = []
    for i in sign_change:
        x0, x1, y0, y1 = grid[i], grid[i + 1], net[i], net[i + 1]
        flip = x0 if y1 == y0 else x0 - y0 * (x1 - x0) / (y1 - y0)
        candidates.append(flip)
    flip = min(candidates, key=lambda x: abs(x - spot))
    return float(flip), n_dropped


def compute_gex(
    snap: ChainSnapshot,
    cfg: GexConfig,
    exclude_0dte: bool = False,
    decision_logger: DecisionLogger | None = None,
) -> GexResult:
    """Compute per-strike and net GEX plus the zero-gamma flip level."""
    df, n_fallback = _chain_frame(snap, cfg, exclude_0dte)

    if df.empty:
        per_strike = pd.DataFrame(columns=["call_gex", "put_gex", "net_gex"])
        net = 0.0
        flip, n_flip_dropped = None, 0
    else:
        spot = snap.underlying_price
        df["gex"] = (df["gamma"] * df["oi"] * cfg.contract_multiplier
                     * spot * spot * 0.01 * df["sign"])
        by_type = df.pivot_table(index="strike", columns="option_type",
                                 values="gex", aggfunc="sum", fill_value=0.0)
        per_strike = pd.DataFrame({
            "call_gex": by_type.get("C", pd.Series(0.0, index=by_type.index)),
            "put_gex": by_type.get("P", pd.Series(0.0, index=by_type.index)),
        })
        per_strike["net_gex"] = per_strike["call_gex"] + per_strike["put_gex"]
        net = float(per_strike["net_gex"].sum())
        flip, n_flip_dropped = _flip_level(df, spot, cfg)

    result = GexResult(
        symbol=snap.symbol,
        spot=snap.underlying_price,
        timestamp=snap.timestamp.isoformat(),
        sign_convention=cfg.dealer_sign_convention,
        exclude_0dte=exclude_0dte,
        max_dte=cfg.max_dte,
        net_gex_per_pct=net,
        flip_level=flip,
        per_strike=per_strike,
        n_quotes_total=len(snap.quotes),
        n_quotes_used=len(df),
        n_gamma_fallback=n_fallback,
        n_flip_rows_dropped=n_flip_dropped,
    )

    if decision_logger is not None:
        decision_logger.log(
            component="gex.engine.compute_gex",
            rule=f"sign_convention.{cfg.dealer_sign_convention}"
                 + (".ex0dte" if exclude_0dte else ".all_expirations"),
            inputs={
                "symbol": snap.symbol,
                "snapshot_ts": snap.timestamp.isoformat(),
                "source": snap.source,
                "spot": snap.underlying_price,
                "quotes_total": len(snap.quotes),
                "quotes_used": len(df),
                "gamma_fallback_rows": n_fallback,
                "flip_rows_dropped_no_iv": n_flip_dropped,
                "max_dte": cfg.max_dte,
                "exclude_0dte": exclude_0dte,
                "risk_free_rate": cfg.risk_free_rate,
            },
            output={
                "net_gex_bn_per_pct": round(result.net_gex_bn_per_pct, 4),
                "flip_level": flip,
                "regime_preview": ("positive_gamma" if net > 0 else
                                   "negative_gamma" if net < 0 else "flat"),
            },
            reason=(
                f"Net GEX = Σ gamma×OI×{cfg.contract_multiplier}×spot²×1% signed by "
                f"'{cfg.dealer_sign_convention}' dealer convention "
                f"({'calls +, puts -' if cfg.dealer_sign_convention == 'standard' else 'calls -, puts +'}); "
                f"flip level from BSM re-pricing on ±{cfg.flip_search_pct:.0%} spot grid "
                f"with sticky-strike IV. Regime label here is informational only — the "
                f"regime GATE is Phase 4 and is not wired to anything yet."
            ),
        )
    return result
