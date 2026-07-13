from datetime import date, datetime, timedelta, timezone

import pytest

from spy_gex_signals.config import GexConfig
from spy_gex_signals.data.models import ChainSnapshot, OptionQuote
from spy_gex_signals.gex.engine import compute_gex

NOW = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
FAR = NOW.date() + timedelta(days=30)


def make_snap(quotes, spot=100.0):
    return ChainSnapshot(symbol="TEST", underlying_price=spot, timestamp=NOW,
                         source="test", quotes=quotes)


def quote(cp, strike, oi, exp=FAR, iv=0.20, gamma=None):
    return OptionQuote(option_type=cp, strike=strike, expiration=exp,
                       open_interest=oi, iv=iv, gamma=gamma)


def test_standard_convention_calls_positive_puts_negative():
    cfg = GexConfig(dealer_sign_convention="standard")
    calls_only = compute_gex(make_snap([quote("C", 100, 1000)]), cfg)
    puts_only = compute_gex(make_snap([quote("P", 100, 1000)]), cfg)
    assert calls_only.net_gex_per_pct > 0
    assert puts_only.net_gex_per_pct < 0


def test_inverse_convention_flips_sign():
    std = compute_gex(make_snap([quote("C", 100, 1000)]),
                      GexConfig(dealer_sign_convention="standard"))
    inv = compute_gex(make_snap([quote("C", 100, 1000)]),
                      GexConfig(dealer_sign_convention="inverse"))
    assert inv.net_gex_per_pct == pytest.approx(-std.net_gex_per_pct)


def test_vendor_gamma_used_when_present():
    cfg = GexConfig()
    r = compute_gex(make_snap([quote("C", 100, 100, gamma=0.02)]), cfg)
    # gamma * OI * mult * spot^2 * 1% = 0.02 * 100 * 100 * 10000 * 0.01
    assert r.net_gex_per_pct == pytest.approx(0.02 * 100 * 100 * 100 * 100 * 0.01)
    assert r.n_gamma_fallback == 0


def test_bsm_fallback_when_vendor_gamma_missing():
    r = compute_gex(make_snap([quote("C", 100, 100, gamma=None, iv=0.20)]), GexConfig())
    assert r.n_gamma_fallback == 1
    assert r.net_gex_per_pct > 0


def test_zero_oi_and_expired_rows_dropped():
    past = NOW.date() - timedelta(days=1)
    r = compute_gex(make_snap([
        quote("C", 100, 0),                # zero OI
        quote("C", 100, 100, exp=past),    # expired
        quote("C", 100, 100),              # kept
    ]), GexConfig())
    assert r.n_quotes_used == 1


def test_exclude_0dte_filter():
    r_all = compute_gex(make_snap([
        quote("C", 100, 100, exp=NOW.date()),  # 0DTE
        quote("C", 100, 100, exp=FAR),
    ]), GexConfig(), exclude_0dte=False)
    r_ex = compute_gex(make_snap([
        quote("C", 100, 100, exp=NOW.date()),
        quote("C", 100, 100, exp=FAR),
    ]), GexConfig(), exclude_0dte=True)
    assert r_all.n_quotes_used == 2
    assert r_ex.n_quotes_used == 1
    assert abs(r_ex.net_gex_per_pct) < abs(r_all.net_gex_per_pct)


def test_flip_level_on_synthetic_chain():
    # Puts clustered below spot, calls above: dealer gamma should be negative
    # when spot sinks toward the put strikes and positive toward the call
    # strikes, giving a zero crossing near current spot.
    snap = make_snap([
        quote("P", 95, 5000, iv=0.20),
        quote("C", 105, 5000, iv=0.20),
    ], spot=100.0)
    r = compute_gex(snap, GexConfig(flip_search_pct=0.10, flip_grid_points=201))
    assert r.flip_level is not None
    assert 96.0 < r.flip_level < 104.0
    # Below the flip the profile must be negative, above it positive
    lo = compute_gex(make_snap(snap.quotes, spot=r.flip_level - 3), GexConfig())
    hi = compute_gex(make_snap(snap.quotes, spot=r.flip_level + 3), GexConfig())
    assert lo.net_gex_per_pct < 0 < hi.net_gex_per_pct


def test_per_strike_aggregation():
    r = compute_gex(make_snap([
        quote("C", 100, 100, gamma=0.02),
        quote("P", 100, 100, gamma=0.02),
        quote("C", 110, 50, gamma=0.01),
    ]), GexConfig())
    assert set(r.per_strike.index) == {100.0, 110.0}
    row = r.per_strike.loc[100.0]
    assert row["net_gex"] == pytest.approx(row["call_gex"] + row["put_gex"])
    assert row["net_gex"] == pytest.approx(0.0, abs=1e-9)  # equal call/put gamma cancels
