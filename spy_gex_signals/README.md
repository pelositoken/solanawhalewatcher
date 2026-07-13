# SPY/Gold Structure + GEX Signal System

Mechanical trade-signal generator for SPY (secondarily gold) combining a
market-structure/liquidity framework with a dealer gamma-exposure (GEX)
regime filter.

---

## ⚠️ GUARDRAILS — READ FIRST

**This system is a decision-support tool only.**

- It **never** places, modifies, or cancels an order anywhere. There is no
  broker/prop-account execution integration, and none will be added to this
  codebase. Its only outputs are logged signals, backtest reports, and
  alerts that a human reads and acts on manually.
- It has **not been validated for live trading** until BOTH the Phase 6
  comparative backtest results AND the Phase 7 forward paper-test period
  have been reviewed and signed off.
- Every trading-relevant decision is written to a structured decision log
  (`logs/decisions.jsonl`) with the rule that fired and the data it used,
  so any signal can be audited after the fact.
- No credentials live in code. Any future provider that needs a key reads
  it from an environment variable (documented below).

---

## Status: Phase 3 (structure-only backtest)

| Phase | Content | Status |
|-------|---------|--------|
| 1 | Price data, options chain, GEX computation + validation | built — awaiting live validation vs published chart |
| 2 | Structure/liquidity signal engine | built — review remediation applied |
| 3 | Structure-only backtest (edge checkpoint) | **built — run on real data pending** |
| 4 | GEX regime gate | not started |
| 5 | Risk & sizing layer (incl. hard kill-switch) | not started |
| 6 | Comparative backtest (gated vs. ungated, walk-forward) | not started |
| 7 | Live signal loop + alerting | not started |

## Data sources (Phase 1 defaults — all swappable)

- **Price:** `yfinance` (free). Daily/1h history is deep; 15m/5m bars are
  capped at ~60 days by Yahoo. Behind a `PriceProvider` interface so
  Polygon/Databento/broker data can replace it via config.
- **Options chain:** CBOE free delayed-quotes endpoint. Full current SPY/GLD
  chain with open interest and greeks, but **no history**. Every fetched
  snapshot is archived to `data/chains/` immediately, so our own history
  starts accruing from day one. A `FileChainProvider` can load
  archived/purchased chain data (e.g. CBOE DataShop, Polygon) for the
  Phase 6 backtest.
- **Gold:** price structure runs on `GC=F` futures bars; the GEX proxy uses
  the **GLD** ETF chain (GC options chains are not freely available).

### Known limitation (flagged per Phase 4 requirement)

With the free CBOE source there are **no historical chain snapshots**, so a
historically-gated GEX backtest can only cover the period we archive
ourselves (or data you buy later). The compensating plan is a longer
forward paper-test in Phase 7. This is surfaced now, before any GEX-gated
results exist, so it can't be mistaken for a validated long history.

## GEX methodology

- Dealer positioning assumption (configurable, `gex.dealer_sign_convention`):
  - `standard` (default): dealers **long calls / short puts** — the
    SqueezeMetrics/SpotGamma convention (customers overwrite calls and buy
    protective puts). Call gamma counts positive, put gamma negative.
  - `inverse`: dealers short calls / long puts — flips every sign.
  The default is `standard` so the validation report can be compared 1:1
  against published SpotGamma/Tradytics charts.
- Per-strike GEX (per 1% move): `gamma × OI × 100 × spot² × 0.01`, signed
  by option type per the convention above. Net GEX is the sum across the
  chain; reported in $Bn per 1% move.
- Zero-gamma flip level: net GEX is recomputed on a grid of hypothetical
  spot levels (Black-Scholes gamma re-evaluated at each level, holding each
  option's implied vol fixed — sticky-strike assumption), and the sign
  crossing is linearly interpolated.
- 0DTE handling: the report shows both all-expirations and 0DTE-excluded
  variants, since published charts differ on this and it is the usual
  reconciliation gap.

## Structure engine (Phase 2)

Implements the user-supplied framework spec mechanically (DOL → MSU →
inducement sweep → CSD → 3R management). Key guardrails, enforced by code
and tests:

- **An MSS is never an entry trigger** — there is no code path from a
  structure break to a signal (double-MSU trap). CSD is the only trigger.
- **Body closes only** — wicks reaching a level never confirm anything.
- CSD rules (`structure.csd_rule`): `50pct` (body close past the sweep
  candle's midpoint — range or body midpoint, configurable) and
  `prior_candle` (body close beyond the SWEEP candle itself — body or
  full-range scope, configurable; always the latest sweep-extreme candle
  under the deeper-grab reset, and clamped to be at least as demanding as
  the 50% midpoint so it is strictly the stronger rule). `both` = either
  fires; **which rule fired is logged on every signal**.
- SMT divergence (SPY↔QQQ, GC=F↔SI=F) is a confidence flag gated behind a
  rolling-correlation check — never a required condition.
- The engine is a bar-by-bar state machine over completed bars and is
  timeframe-parameterized (`structure.timeframe_pairs`), so a historical
  run is mechanically identical to a live run.
- Every sweep considered, every rejection (with reason), and every signal
  goes to the decision log.

The GEX↔DOL alignment hook exists (`HtfContext.check_dol_gex_alignment`)
and reports `not_wired` until Phase 4.

## Setup

```bash
cd spy_gex_signals
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
```

No API keys are required for the Phase 1 defaults. Future providers will
read keys from environment variables (e.g. `POLYGON_API_KEY`) — never from
code or config files.

## Usage

```bash
# Fetch + archive a chain snapshot and print a GEX summary
python scripts/fetch_snapshot.py --symbol SPY

# Full validation report (net GEX, flip level, top strikes, 0DTE variants)
# for side-by-side comparison with a SpotGamma/Tradytics chart
python scripts/validate_gex.py --symbol SPY

# Re-run the report on an archived snapshot instead of a live fetch
python scripts/validate_gex.py --file data/chains/SPY/2026-07-13/193000Z.json.gz

# Sanity-check the price layer (bar counts per timeframe)
python scripts/check_price_data.py

# Structure engine over history: every signal + every rejection and why
python scripts/scan_structure.py                # all instruments/timeframe pairs
python scripts/scan_structure.py --symbol SPY
python scripts/scan_structure.py --report       # + full markdown eyeball report
python scripts/scan_structure.py --fixture      # deterministic synthetic demo, no network

# Phase 3: structure-only backtest (NO GEX), Entry-1 only, per-pair metrics,
# max_bars_sweep_to_csd sensitivity at 3/5/8, edge-checkpoint verdict
python scripts/backtest_structure.py
python scripts/backtest_structure.py --symbol SPY
python scripts/backtest_structure.py --fixture  # pipeline smoke test, no network
```

### Data-source timing (Databento / GEX)

- **Phase 3** needs price bars only — yfinance works free (2y of 1h; 5m capped
  at ~60 days, so the 1h/5m pair has a thin sample). A Databento key
  (`DATABENTO_API_KEY` env var, provider not yet implemented) would lift the
  intraday cap and provide real GC futures bars.
- **Phase 4** needs current chains only — free CBOE feed already built.
- **Phase 6** is where historical options data binds: the gated-vs-ungated
  comparison window equals your historical GEX coverage (Databento OPRA,
  Polygon options, or CBOE DataShop — else only self-archived snapshots).

Reports are written to `reports/`, logs to `logs/`.

## Tests

```bash
python -m pytest tests/ -q
```
