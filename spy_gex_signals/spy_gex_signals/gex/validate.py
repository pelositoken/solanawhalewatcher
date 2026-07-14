"""GEX validation report.

Produces a markdown report designed for side-by-side comparison with a
published SpotGamma/Tradytics chart for the same day, so the computation
can be sanity-checked before anything downstream trusts it (Phase 1
requirement). Shows both all-expirations and 0DTE-excluded variants since
that is the usual source of disagreement between published charts.
"""

from __future__ import annotations

from ..config import GexConfig
from ..data.models import ChainSnapshot
from ..decision_log import DecisionLogger
from .engine import GexResult, compute_gex


def _fmt_strike_table(result: GexResult, n: int) -> str:
    top = result.top_strikes(n)
    lines = [
        "| Strike | Call GEX ($M/1%) | Put GEX ($M/1%) | Net GEX ($M/1%) |",
        "|--------|-----------------:|----------------:|----------------:|",
    ]
    for strike, row in top.iterrows():
        lines.append(
            f"| {strike:g} | {row['call_gex'] / 1e6:,.1f} | "
            f"{row['put_gex'] / 1e6:,.1f} | {row['net_gex'] / 1e6:,.1f} |"
        )
    return "\n".join(lines)


def _fmt_variant(title: str, r: GexResult) -> str:
    flip = f"{r.flip_level:,.2f}" if r.flip_level is not None else "no crossing within search range"
    regime = "POSITIVE gamma" if r.net_gex_per_pct > 0 else "NEGATIVE gamma" if r.net_gex_per_pct < 0 else "flat"
    return "\n".join([
        f"### {title}",
        "",
        f"- Net GEX: **${r.net_gex_bn_per_pct:,.3f} Bn per 1% move** ({regime})",
        f"- Zero-gamma flip level: **{flip}**",
        f"- Quotes used: {r.n_quotes_used} of {r.n_quotes_total} "
        f"(OI=0/expired/filtered rows dropped; {r.n_gamma_fallback} rows priced via "
        f"Black-Scholes fallback; {r.n_flip_rows_dropped} rows had no usable IV for the flip search)",
        "",
        f"Top {min(len(r.per_strike), 5)} gamma strikes by |net GEX|:",
        "",
        _fmt_strike_table(r, 5),
    ])


def build_validation_report(
    snap: ChainSnapshot,
    cfg: GexConfig,
    decision_logger: DecisionLogger | None = None,
) -> str:
    all_exp = compute_gex(snap, cfg, exclude_0dte=False, decision_logger=decision_logger)
    ex_0dte = compute_gex(snap, cfg, exclude_0dte=True, decision_logger=decision_logger)

    convention_desc = (
        "dealers long calls / short puts (SqueezeMetrics/SpotGamma standard; call gamma +, put gamma −)"
        if cfg.dealer_sign_convention == "standard"
        else "dealers short calls / long puts (INVERSE of published convention — signs are mirrored vs. SpotGamma)"
    )

    return "\n".join([
        f"# GEX validation report — {snap.symbol}",
        "",
        f"- Snapshot: {snap.timestamp.isoformat()} (source: {snap.source}"
        + (", ~15min delayed feed)" if snap.source == "cboe_delayed" else ")"),
        f"- Underlying price: **{snap.underlying_price:,.2f}**",
        f"- Dealer sign convention: `{cfg.dealer_sign_convention}` — {convention_desc}",
        f"- Expiration filter: max_dte={cfg.max_dte if cfg.max_dte is not None else 'none (all listed)'}",
        "",
        _fmt_variant("All expirations", all_exp),
        "",
        _fmt_variant("Excluding 0DTE", ex_0dte),
        "",
        "## How to sanity-check this against a published chart",
        "",
        "Pull up a SpotGamma / Tradytics / similar GEX chart for the same trading day and compare:",
        "",
        "1. **Sign of net GEX** should match the published regime call (positive/negative gamma).",
        "2. **Net GEX magnitude** should be the same order of magnitude. Vendors differ on",
        "   0DTE inclusion, OI update timing (OI is set once daily, pre-market), and",
        "   spot-vs-forward conventions — expect ±30–50% divergence, not sign flips.",
        "   Compare against whichever variant (all-exp vs ex-0DTE) the vendor uses.",
        "3. **Flip level** should be within a few dollars of the published \"zero gamma\"/\"vol trigger\" level.",
        "4. **Top gamma strikes** should substantially overlap the published \"key gamma levels\"",
        "   (call wall / put wall usually sit among the top absolute-GEX strikes).",
        "",
        "If sign or flip level disagree materially, do NOT proceed to the Phase 4 gate —",
        "flag it for investigation first.",
    ])
