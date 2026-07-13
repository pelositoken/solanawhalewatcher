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


@dataclass(frozen=True)
class PriceConfig:
    provider: str = "yfinance"
    timeframes: tuple[str, ...] = ("1d", "1h", "15m", "5m")


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
    logging_cfg = LoggingConfig(**(raw.get("logging") or {}))
    return Config(instruments=instruments, price=price, chain=chain, gex=gex, logging=logging_cfg)
