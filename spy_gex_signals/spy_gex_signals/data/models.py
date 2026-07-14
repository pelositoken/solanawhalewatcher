"""Data models for chain snapshots and option quotes.

Price bars are plain pandas DataFrames (UTC-indexed, columns
open/high/low/close/volume) — see price_provider.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone

# OCC option symbol: ROOT + yymmdd + C/P + strike*1000 zero-padded to 8
_OCC_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<date>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ_symbol(symbol: str) -> tuple[str, date, str, float]:
    """Parse an OCC option symbol into (root, expiration, 'C'|'P', strike)."""
    m = _OCC_RE.match(symbol.strip().upper())
    if not m:
        raise ValueError(f"Not a valid OCC option symbol: {symbol!r}")
    d = m.group("date")
    expiration = date(2000 + int(d[:2]), int(d[2:4]), int(d[4:6]))
    strike = int(m.group("strike")) / 1000.0
    return m.group("root"), expiration, m.group("cp"), strike


@dataclass
class OptionQuote:
    option_type: str  # 'C' or 'P'
    strike: float
    expiration: date
    open_interest: int = 0
    volume: int = 0
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    iv: float | None = None      # implied volatility, decimal (0.18 = 18%)
    delta: float | None = None   # vendor greek, if provided
    gamma: float | None = None   # vendor greek, if provided
    occ_symbol: str | None = None

    def __post_init__(self) -> None:
        if self.option_type not in ("C", "P"):
            raise ValueError(f"option_type must be 'C' or 'P', got {self.option_type!r}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expiration"] = self.expiration.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OptionQuote":
        d = dict(d)
        d["expiration"] = date.fromisoformat(d["expiration"])
        return cls(**d)


@dataclass
class ChainSnapshot:
    symbol: str                  # chain symbol (e.g. SPY, GLD)
    underlying_price: float
    timestamp: datetime          # when the snapshot was taken (UTC)
    source: str                  # provider identifier
    quotes: list[OptionQuote] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)

    @property
    def snapshot_date(self) -> date:
        """Trading date of the snapshot in US/Eastern terms is approximated by
        the UTC date; good enough for 0DTE classification during RTH."""
        return self.timestamp.date()

    def expirations(self) -> list[date]:
        return sorted({q.expiration for q in self.quotes})

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "underlying_price": self.underlying_price,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "quotes": [q.to_dict() for q in self.quotes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChainSnapshot":
        return cls(
            symbol=d["symbol"],
            underlying_price=d["underlying_price"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            source=d["source"],
            quotes=[OptionQuote.from_dict(q) for q in d["quotes"]],
        )
