from datetime import date, datetime, timezone

from spy_gex_signals.data.archiver import ChainArchiver
from spy_gex_signals.data.chain_provider import FileChainProvider
from spy_gex_signals.data.models import ChainSnapshot, OptionQuote


def make_snap():
    return ChainSnapshot(
        symbol="SPY",
        underlying_price=620.25,
        timestamp=datetime(2026, 7, 13, 19, 30, 0, tzinfo=timezone.utc),
        source="cboe_delayed",
        quotes=[
            OptionQuote(option_type="C", strike=620.0, expiration=date(2026, 7, 17),
                        open_interest=1234, volume=10, bid=1.0, ask=1.1,
                        iv=0.18, delta=0.5, gamma=0.03, occ_symbol="SPY260717C00620000"),
            OptionQuote(option_type="P", strike=600.0, expiration=date(2026, 8, 21),
                        open_interest=999, iv=0.22, gamma=0.01),
        ],
    )


def test_archive_round_trip(tmp_path):
    arch = ChainArchiver(tmp_path)
    snap = make_snap()
    path = arch.save(snap)

    assert path.exists()
    assert "SPY/2026-07-13" in str(path)

    loaded = arch.load(path)
    assert loaded.symbol == snap.symbol
    assert loaded.underlying_price == snap.underlying_price
    assert loaded.timestamp == snap.timestamp
    assert len(loaded.quotes) == 2
    assert loaded.quotes[0].to_dict() == snap.quotes[0].to_dict()

    assert arch.list_snapshots("SPY") == [path]
    assert arch.list_snapshots("GLD") == []


def test_file_chain_provider_reads_archive(tmp_path):
    arch = ChainArchiver(tmp_path)
    path = arch.save(make_snap())
    loaded = FileChainProvider(path).fetch("SPY")
    assert loaded.symbol == "SPY"
    assert len(loaded.quotes) == 2
