"""Black-Scholes gamma, used two ways:

1. Fallback when a chain row is missing a vendor gamma.
2. Re-pricing gamma at hypothetical spot levels for the zero-gamma flip
   search (each option's implied vol held fixed — sticky-strike assumption).

Gamma is identical for calls and puts in Black-Scholes.
"""

from __future__ import annotations

import math

import numpy as np


def bs_gamma(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
    r: float = 0.0,
    q: float = 0.0,
) -> float:
    """Black-Scholes gamma per share (dDelta/dSpot).

    Returns 0.0 for degenerate inputs (expired, zero vol, non-positive
    prices) rather than raising — an option that can't be priced
    contributes no gamma.
    """
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv is None or iv <= 0:
        return 0.0
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return math.exp(-q * t_years) * pdf / (spot * iv * sqrt_t)


def bs_gamma_vec(
    spot: float,
    strikes: np.ndarray,
    t_years: np.ndarray,
    ivs: np.ndarray,
    r: float = 0.0,
    q: float = 0.0,
) -> np.ndarray:
    """Vectorized bs_gamma over arrays of strikes/expiries/vols at one spot."""
    strikes = np.asarray(strikes, dtype=float)
    t_years = np.asarray(t_years, dtype=float)
    ivs = np.asarray(ivs, dtype=float)

    out = np.zeros_like(strikes)
    ok = (strikes > 0) & (t_years > 0) & (ivs > 0) & (spot > 0)
    if not ok.any():
        return out
    k, t, v = strikes[ok], t_years[ok], ivs[ok]
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / k) + (r - q + 0.5 * v * v) * t) / (v * sqrt_t)
    pdf = np.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    out[ok] = np.exp(-q * t) * pdf / (spot * v * sqrt_t)
    return out
