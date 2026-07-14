from datetime import date

import pytest

from spy_gex_signals.data.models import parse_occ_symbol


def test_parse_occ_symbol():
    root, exp, cp, strike = parse_occ_symbol("SPY261218C00500000")
    assert root == "SPY"
    assert exp == date(2026, 12, 18)
    assert cp == "C"
    assert strike == 500.0


def test_parse_occ_fractional_strike():
    _, _, cp, strike = parse_occ_symbol("GLD260116P00312500")
    assert cp == "P"
    assert strike == 312.5


def test_parse_occ_rejects_garbage():
    with pytest.raises(ValueError):
        parse_occ_symbol("NOT_AN_OPTION")
