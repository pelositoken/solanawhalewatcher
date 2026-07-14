"""Options chain providers.

Default is CBOE's free delayed-quotes JSON endpoint: full current chain
with open interest and vendor greeks, ~15 min delayed, NO history. Every
fetch should be archived immediately (see archiver.py) so our own history
accrues from day one.

FileChainProvider loads archived or user-supplied snapshots, which is also
the path a purchased historical dataset (CBOE DataShop, Polygon options)
will use for the Phase 6 backtest.
"""

from __future__ import annotations

import gzip
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

import requests

from .models import ChainSnapshot, OptionQuote, parse_occ_symbol

log = logging.getLogger(__name__)

# ETFs/equities use the bare ticker; indices use an underscore prefix (_SPX).
_CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"


class ChainProvider(ABC):
    name: str

    @abstractmethod
    def fetch(self, chain_symbol: str) -> ChainSnapshot:
        """Return the current (or loaded) chain snapshot for the symbol."""


class CboeDelayedProvider(ChainProvider):
    name = "cboe_delayed"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def fetch(self, chain_symbol: str) -> ChainSnapshot:
        sym = chain_symbol.upper()
        data = None
        tried = []
        for candidate in (sym, f"_{sym}"):
            url = _CBOE_URL.format(sym=candidate)
            tried.append(url)
            resp = requests.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                break
        if data is None:
            raise RuntimeError(f"CBOE delayed chain not found for {sym} (tried {tried})")

        payload = data.get("data", {})
        spot = payload.get("current_price") or payload.get("close")
        if not spot:
            raise RuntimeError(f"CBOE response for {sym} has no underlying price")

        quotes: list[OptionQuote] = []
        skipped = 0
        for row in payload.get("options", []):
            occ = row.get("option", "")
            try:
                _, expiration, cp, strike = parse_occ_symbol(occ)
            except ValueError:
                skipped += 1
                continue
            quotes.append(
                OptionQuote(
                    option_type=cp,
                    strike=strike,
                    expiration=expiration,
                    open_interest=int(row.get("open_interest") or 0),
                    volume=int(row.get("volume") or 0),
                    bid=row.get("bid"),
                    ask=row.get("ask"),
                    last=row.get("last_trade_price"),
                    iv=row.get("iv"),
                    delta=row.get("delta"),
                    gamma=row.get("gamma"),
                    occ_symbol=occ,
                )
            )
        if skipped:
            log.warning("CBOE %s: skipped %d rows with unparseable OCC symbols", sym, skipped)

        snap = ChainSnapshot(
            symbol=sym,
            underlying_price=float(spot),
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            quotes=quotes,
        )
        log.info("CBOE %s: %d option quotes across %d expirations, spot=%.2f",
                 sym, len(quotes), len(snap.expirations()), snap.underlying_price)
        return snap


class FileChainProvider(ChainProvider):
    """Load a snapshot from a .json or .json.gz file in our archive format."""

    name = "file"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self, chain_symbol: str = "") -> ChainSnapshot:
        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "rt") as f:
            snap = ChainSnapshot.from_dict(json.load(f))
        if chain_symbol and snap.symbol.upper() != chain_symbol.upper():
            raise ValueError(
                f"Snapshot file {self.path} is for {snap.symbol}, not {chain_symbol}"
            )
        return snap


def make_chain_provider(name: str) -> ChainProvider:
    if name == "cboe_delayed":
        return CboeDelayedProvider()
    raise ValueError(f"Unknown chain provider: {name!r} (available: cboe_delayed, file)")
