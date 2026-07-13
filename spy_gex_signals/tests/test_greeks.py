import numpy as np
import pytest

from spy_gex_signals.gex.greeks import bs_gamma, bs_gamma_vec


def test_atm_gamma_known_value():
    # S=K=100, r=q=0, sigma=0.20, T=1y:
    # d1 = 0.1, phi(0.1)=0.3969525..., gamma = phi(d1)/(S*sigma*sqrt(T)) = 0.01984763
    assert bs_gamma(100, 100, 1.0, 0.20) == pytest.approx(0.0198476, abs=1e-6)


def test_gamma_peaks_near_atm():
    atm = bs_gamma(100, 100, 0.25, 0.20)
    otm = bs_gamma(100, 130, 0.25, 0.20)
    itm = bs_gamma(100, 70, 0.25, 0.20)
    assert atm > otm and atm > itm


def test_degenerate_inputs_return_zero():
    assert bs_gamma(100, 100, 0.0, 0.20) == 0.0    # expired
    assert bs_gamma(100, 100, 1.0, 0.0) == 0.0     # zero vol
    assert bs_gamma(100, 100, 1.0, None) == 0.0    # missing vol
    assert bs_gamma(0, 100, 1.0, 0.20) == 0.0      # bad spot


def test_vectorized_matches_scalar():
    strikes = np.array([90.0, 100.0, 110.0, 0.0])
    ts = np.array([0.5, 0.5, 0.5, 0.5])
    ivs = np.array([0.25, 0.20, 0.22, 0.20])
    vec = bs_gamma_vec(100.0, strikes, ts, ivs, r=0.04)
    for i in range(4):
        assert vec[i] == pytest.approx(
            bs_gamma(100.0, strikes[i], ts[i], ivs[i], r=0.04), abs=1e-12
        )
