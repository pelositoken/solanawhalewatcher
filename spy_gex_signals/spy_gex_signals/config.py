"""Typed configuration loader.

Secrets never live in config.yaml; any future keyed provider must read its
key from an environment variable and document it in the README.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

VALID_SIGN_CONVENTIONS = ("standard", "inverse")


@dataclass(frozen=True)
class InstrumentConfig:
    name: str
    price_symbol: str
    chain_symbol: str
    databento: dict | None = None   # {dataset, symbol, stype_in?}


@dataclass(frozen=True)
class PriceConfig:
    provider: str = "yfinance"
    timeframes: tuple[str, ...] = ("1d", "1h", "15m", "5m")
    databento_symbols: dict = field(default_factory=dict)
    databento_intraday_lookback_days: int = 365


@dataclass(frozen=True)
class ChainConfig:
    provider: str = "cboe_delayed"
    archive_dir: str = "data/chains"


@dataclass(frozen=True)
class GexConfig:
    dealer_sign_convention: str = "standard"
    risk_free_rate: float = 0.045
    contract_multiplier: int = 100
    flip_search_pct: float = 0.10
    flip_grid_points: int = 81
    max_dte: int | None = None
    top_n_strikes: int = 5
    dol_strike_tolerance_pct: float = 0.005

    def __post_init__(self) -> None:
        if self.dealer_sign_convention not in VALID_SIGN_CONVENTIONS:
            raise ValueError(
                f"gex.dealer_sign_convention must be one of {VALID_SIGN_CONVENTIONS}, "
                f"got {self.dealer_sign_convention!r}"
            )


VALID_CSD_RULES = ("50pct", "prior_candle", "both")
VALID_MIDPOINTS = ("range", "body")
VALID_PRIOR_SCOPES = ("body", "full")
VALID_ENTRY_TYPES = ("immediate", "fvg_retest")


@dataclass(frozen=True)
class SmtConfig:
    enabled: bool = True
    pairs: dict[str, str] = field(default_factory=dict)
    min_correlation: float = 0.7
    correlation_lookback_bars: int = 200


@dataclass(frozen=True)
class StructureConfig:
    timeframe_pairs: tuple[tuple[str, str], ...] = (("1d", "1h"), ("1h", "5m"))
    swing_strength: int = 2
    csd_rule: str = "both"
    sweep_candle_midpoint: str = "range"
    prior_candle_scope: str = "body"
    entry_type: str = "immediate"
    fvg_retest_expiry_bars: int = 20
    invalidation_buffer_pct: float = 0.0005
    max_bars_sweep_to_csd: int = 5
    high_visibility_atr_mult: float = 2.0
    atr_period: int = 14
    require_subtf_confirmation: bool = False
    smt: SmtConfig = field(default_factory=SmtConfig)

    def __post_init__(self) -> None:
        if self.csd_rule not in VALID_CSD_RULES:
            raise ValueError(f"structure.csd_rule must be one of {VALID_CSD_RULES}")
        if self.sweep_candle_midpoint not in VALID_MIDPOINTS:
            raise ValueError(f"structure.sweep_candle_midpoint must be one of {VALID_MIDPOINTS}")
        if self.prior_candle_scope not in VALID_PRIOR_SCOPES:
            raise ValueError(f"structure.prior_candle_scope must be one of {VALID_PRIOR_SCOPES}")
        if self.entry_type not in VALID_ENTRY_TYPES:
            raise ValueError(f"structure.entry_type must be one of {VALID_ENTRY_TYPES}")
        if self.swing_strength < 1:
            raise ValueError("structure.swing_strength must be >= 1")


@dataclass(frozen=True)
class LoggingConfig:
    dir: str = "logs"
    decision_log: str = "logs/decisions.jsonl"


@dataclass(frozen=True)
class Config:
    instruments: dict[str, InstrumentConfig] = field(default_factory=dict)
    price: PriceConfig = field(default_factory=PriceConfig)
    chain: ChainConfig = field(default_factory=ChainConfig)
    gex: GexConfig = field(default_factory=GexConfig)
    structure: StructureConfig = field(default_factory=StructureConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def resolve_path(self, rel: str) -> Path:
        """Resolve a config-relative path against the project root."""
        p = Path(rel)
        return p if p.is_absolute() else PROJECT_ROOT / p

    def instrument_for_chain_symbol(self, chain_symbol: str) -> InstrumentConfig | None:
        for inst in self.instruments.values():
            if inst.chain_symbol.upper() == chain_symbol.upper():
                return inst
        return None

    def databento_mapping(self, price_symbol: str) -> dict | None:
        """Databento {dataset, symbol, stype_in?} for a price symbol, from the
        instrument config or the price.databento_symbols fallback map."""
        for inst in self.instruments.values():
            if inst.price_symbol == price_symbol and inst.databento:
                return inst.databento
        return self.price.databento_symbols.get(price_symbol)


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else Path(os.environ.get("SGS_CONFIG", DEFAULT_CONFIG_PATH))
    with open(cfg_path) as f:
        raw = yaml.safe_load(f) or {}

    instruments = {
        name: InstrumentConfig(name=name, **spec)
        for name, spec in (raw.get("instruments") or {}).items()
    }
    price = PriceConfig(**{**(raw.get("price") or {}),
                           "timeframes": tuple((raw.get("price") or {}).get("timeframes", ("1d", "1h", "15m", "5m")))})
    chain = ChainConfig(**(raw.get("chain") or {}))
    gex = GexConfig(**(raw.get("gex") or {}))

    raw_structure = dict(raw.get("structure") or {})
    smt = SmtConfig(**(raw_structure.pop("smt", None) or {}))
    pairs = raw_structure.pop("timeframe_pairs", None)
    if pairs is not None:
        raw_structure["timeframe_pairs"] = tuple(
            (p["htf"], p["ltf"]) if isinstance(p, dict) else tuple(p) for p in pairs
        )
    structure = StructureConfig(**raw_structure, smt=smt)

    logging_cfg = LoggingConfig(**(raw.get("logging") or {}))
    return Config(instruments=instruments, price=price, chain=chain, gex=gex,
                  structure=structure, logging=logging_cfg)
