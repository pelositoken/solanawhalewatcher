"""DatabentoPriceProvider tests — fully mocked, no key, no network.

A fake `databento` module is injected into sys.modules; the fake client
records call order so the cost-estimate-before-fetch guardrail is asserted,
not assumed.
"""

import sys
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from spy_gex_signals.data.price_provider import (
    DatabentoPriceProvider, make_price_provider, resample_ohlcv,
)

T0 = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)  # safely in the past


def bars_1m(specs, start=T0, step=timedelta(minutes=1)):
    """specs: list of (offset_minutes, o, h, l, c, v[, raw_symbol])"""
    rows, idx = [], []
    for s in specs:
        off, o, h, l, c, v = s[:6]
        idx.append(start + off * step)
        row = {"open": o, "high": h, "low": l, "close": c, "volume": v}
        if len(s) > 6:
            row["symbol"] = s[6]
        rows.append(row)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx, tz="UTC"))


class FakeClient:
    def __init__(self, df, cost=0.0123):
        self.df = df
        self.cost = cost
        self.calls = []
        outer = self

        class _Meta:
            def get_cost(self, **kw):
                outer.calls.append(("get_cost", kw))
                return outer.cost

        class _Store:
            def to_df(self, map_symbols=True):
                return outer.df.copy()

        class _Ts:
            def get_range(self, **kw):
                outer.calls.append(("get_range", kw))
                return _Store()

        self.metadata = _Meta()
        self.timeseries = _Ts()


@pytest.fixture
def fake_databento(monkeypatch):
    """Inject fake module + env var; returns a setter for the fetch result df."""
    holder = {}
    mod = types.ModuleType("databento")
    mod.Historical = lambda *a, **k: holder["client"]
    monkeypatch.setitem(sys.modules, "databento", mod)
    monkeypatch.setenv("DATABENTO_API_KEY", "db-TEST-not-a-real-key")

    def make(df, cost=0.0123):
        holder["client"] = FakeClient(df, cost)
        return holder["client"]

    return make


def make_provider(mapping):
    return DatabentoPriceProvider(symbol_mapping=lambda s: mapping.get(s),
                                  intraday_lookback_days=365)


def test_missing_key_raises_named_error(monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DATABENTO_API_KEY"):
        DatabentoPriceProvider(symbol_mapping=lambda s: None)


def test_missing_mapping_raises(fake_databento):
    fake_databento(bars_1m([(0, 1, 2, 1, 2, 10)]))
    provider = make_provider({})
    with pytest.raises(ValueError, match="No Databento mapping"):
        provider.get_history("XYZ", "1h")


def test_cost_estimated_before_fetch_and_accumulated(fake_databento, capsys):
    client = fake_databento(bars_1m([(0, 1, 2, 1, 2, 10)]), cost=0.05)
    provider = make_provider({"SPY": {"dataset": "DBEQ.BASIC", "symbol": "SPY"}})
    provider.get_history("SPY", "1h")
    assert [c[0] for c in client.calls] == ["get_cost", "get_range"]  # order matters
    provider.get_history("SPY", "1d")
    out = capsys.readouterr().out
    assert "estimated $0.0500" in out
    assert "session data spend so far: ~$0.1000" in out


def test_dataset_and_symbology_routed(fake_databento):
    client = fake_databento(bars_1m([(0, 1, 2, 1, 2, 10, "GCQ6")]))
    provider = make_provider(
        {"GC=F": {"dataset": "GLBX.MDP3", "symbol": "GC.c.0", "stype_in": "continuous"}})
    provider.get_history("GC=F", "1h")
    kw = client.calls[-1][1]
    assert kw["dataset"] == "GLBX.MDP3"
    assert kw["symbols"] == ["GC.c.0"]
    assert kw["stype_in"] == "continuous"
    assert kw["schema"] == "ohlcv-1h"


def test_roll_dates_detected_on_contract_change(fake_databento):
    df = bars_1m([
        (0, 10, 11, 9, 10, 5, "GCM6"),
        (1, 10, 11, 9, 10, 5, "GCM6"),
        (2, 12, 13, 11, 12, 5, "GCQ6"),   # roll here
        (3, 12, 13, 11, 12, 5, "GCQ6"),
    ])
    fake_databento(df)
    provider = make_provider(
        {"GC=F": {"dataset": "GLBX.MDP3", "symbol": "GC.c.0", "stype_in": "continuous"}})
    provider.get_history("GC=F", "1m")
    rolls = provider.get_roll_dates("GC=F")
    assert len(rolls) == 1
    assert rolls[0] == df.index[2]


def test_no_roll_dates_for_equities(fake_databento):
    fake_databento(bars_1m([(0, 1, 2, 1, 2, 10), (1, 1, 2, 1, 2, 10)]))
    provider = make_provider({"SPY": {"dataset": "DBEQ.BASIC", "symbol": "SPY"}})
    provider.get_history("SPY", "1m")
    assert provider.get_roll_dates("SPY") == []


def test_resample_reference_day():
    """Hand-computed 1m→5m reference including a missing-minutes gap.

    [13:30,13:35): bars at :30 :31 :32 → o=10 h=14 l=9 c=13 v=600, label 13:30
    [13:35,13:40): bar at :36 only  → o=13 h=15 l=12 c=14 v=400, label 13:35
    [13:40,13:45): no bars → bin DROPPED
    [13:45,13:50): bar at :45      → o=14 h=16 l=13 c=15 v=250, label 13:45
    """
    df = bars_1m([
        (0, 10, 12, 9, 11, 100),
        (1, 11, 13, 10, 12, 200),
        (2, 12, 14, 11, 13, 300),
        (6, 13, 15, 12, 14, 400),
        (15, 14, 16, 13, 15, 250),
    ])
    out = resample_ohlcv(df, "5m")
    assert list(out.index) == [pd.Timestamp("2026-06-01 13:30", tz="UTC"),
                               pd.Timestamp("2026-06-01 13:35", tz="UTC"),
                               pd.Timestamp("2026-06-01 13:45", tz="UTC")]
    b0 = out.iloc[0]
    assert (b0.open, b0.high, b0.low, b0.close, b0.volume) == (10, 14, 9, 13, 600)
    b1 = out.iloc[1]
    assert (b1.open, b1.high, b1.low, b1.close, b1.volume) == (13, 15, 12, 14, 400)
    b2 = out.iloc[2]
    assert (b2.open, b2.high, b2.low, b2.close, b2.volume) == (14, 16, 13, 15, 250)


def test_5m_interval_fetches_1m_and_resamples(fake_databento):
    df = bars_1m([(i, 10 + i, 11 + i, 9 + i, 10.5 + i, 100) for i in range(10)])
    client = fake_databento(df)
    provider = make_provider({"SPY": {"dataset": "DBEQ.BASIC", "symbol": "SPY"}})
    out = provider.get_history("SPY", "5m")
    assert client.calls[-1][1]["schema"] == "ohlcv-1m"
    assert len(out) == 2  # 10 minutes → two 5m bars
    assert out.iloc[0]["volume"] == 500


def test_latest_quote_returns_as_of_timestamp(fake_databento):
    df = bars_1m([(0, 10, 11, 9, 10, 5), (1, 10, 12, 9.5, 11.5, 5)])
    fake_databento(df)
    provider = make_provider({"SPY": {"dataset": "DBEQ.BASIC", "symbol": "SPY"}})
    price, as_of = provider.get_latest_quote("SPY")
    assert price == 11.5
    assert as_of == df.index[1].to_pydatetime() + timedelta(minutes=1)  # bar CLOSE time


def test_factory_requires_config():
    with pytest.raises(ValueError, match="needs the loaded Config"):
        make_price_provider("databento")
