import numpy as np
import pytest

from option_screener.pricing import (binomial_price, bs_greeks, bs_price, implied_carry,
                                     implied_forward, implied_vol, option_price, price_bounds)


def test_bs_hull_reference_values():
    # Hull, Options Futures and Other Derivatives : S=42, K=40, r=10 %, sigma=20 %, T=0,5
    assert bs_price(42, 40, 0.5, 0.10, 0.0, 0.20, True) == pytest.approx(4.76, abs=0.005)
    assert bs_price(42, 40, 0.5, 0.10, 0.0, 0.20, False) == pytest.approx(0.81, abs=0.005)


def test_put_call_parity_with_dividend():
    S, K, T, r, q, sig = 100.0, np.array([80, 100, 120.0]), 0.75, 0.04, 0.02, 0.3
    c = bs_price(S, K, T, r, q, sig, True)
    p = bs_price(S, K, T, r, q, sig, False)
    np.testing.assert_allclose(c - p, S * np.exp(-q * T) - K * np.exp(-r * T), atol=1e-10)


def test_greeks_match_finite_differences():
    S, K, T, r, q, sig = 100.0, 105.0, 0.4, 0.03, 0.01, 0.25
    for is_call in (True, False):
        g = bs_greeks(S, K, T, r, q, sig, is_call)
        h = 0.01
        delta = (bs_price(S + h, K, T, r, q, sig, is_call) - bs_price(S - h, K, T, r, q, sig, is_call)) / (2 * h)
        gamma = (bs_price(S + h, K, T, r, q, sig, is_call) - 2 * bs_price(S, K, T, r, q, sig, is_call)
                 + bs_price(S - h, K, T, r, q, sig, is_call)) / h**2
        vega = (bs_price(S, K, T, r, q, sig + 1e-4, is_call) - bs_price(S, K, T, r, q, sig - 1e-4, is_call)) / 2e-4 / 100
        dt = 1 / 365
        theta = bs_price(S, K, T - dt, r, q, sig, is_call) - bs_price(S, K, T, r, q, sig, is_call)
        assert g["delta"] == pytest.approx(delta, abs=1e-6)
        assert g["gamma"] == pytest.approx(gamma, rel=1e-3)
        assert g["vega"] == pytest.approx(vega, rel=1e-5)
        assert g["theta"] == pytest.approx(theta, rel=2e-2)


def test_european_tree_converges_to_bs():
    S, K, T, r, q, sig = 100.0, np.array([90, 100, 110.0]), 0.5, 0.05, 0.01, 0.3
    for is_call in (True, False):
        tree = binomial_price(S, K, T, r, q, sig, is_call, american=False, steps=800, control_variate=False)
        np.testing.assert_allclose(tree, bs_price(S, K, T, r, q, sig, is_call), atol=0.01)


def test_american_put_reference_and_bounds():
    # Hull : S=50, K=50, r=10 %, sigma=40 %, T=5 mois -> put américain ~ 4,28
    am = binomial_price(50, 50, 5 / 12, 0.10, 0.0, 0.40, False, american=True, steps=500)
    assert am == pytest.approx(4.28, abs=0.02)
    assert am > bs_price(50, 50, 5 / 12, 0.10, 0.0, 0.40, False)


def test_american_call_without_dividend_equals_european():
    tree = binomial_price(100, 95, 1.0, 0.05, 0.0, 0.25, True, american=True, steps=800,
                          control_variate=False)
    assert tree == pytest.approx(bs_price(100, 95, 1.0, 0.05, 0.0, 0.25, True), abs=0.01)


def test_deep_itm_american_put_is_at_least_intrinsic():
    p = option_price(60, 100, 1.0, 0.05, 0.0, 0.2, False, american=True)
    assert p >= 40 - 1e-9


@pytest.mark.parametrize("american", [False, True])
def test_implied_vol_round_trip(american):
    rng = np.random.default_rng(1)
    n = 600
    S = 100.0
    K = rng.uniform(70, 130, n)
    T = rng.uniform(7 / 365, 1.5, n)
    sig = rng.uniform(0.08, 0.9, n)
    is_call = rng.random(n) < 0.5
    price = option_price(S, K, T, 0.04, 0.01, sig, is_call, american)
    lower, _ = price_bounds(S, K, T, 0.04, 0.01, is_call, american)
    meaningful = price - lower > 0.05  # sinon l'IV n'est pas identifiable
    iv = implied_vol(price, S, K, T, 0.04, 0.01, is_call, american)
    assert np.all(np.isfinite(iv[meaningful]))
    tol = 1e-4 if not american else 2e-3
    np.testing.assert_allclose(iv[meaningful], sig[meaningful], atol=tol)


def test_implied_vol_rejects_arbitrage_violations():
    # sous la valeur intrinsèque, au-dessus du spot, prix nul, échu
    iv = implied_vol(np.array([5.0, 150.0, 0.0, 3.0]), 100, np.array([90, 90, 100, 100.0]),
                     np.array([0.5, 0.5, 0.5, 0.0]), 0.03, 0.0, True)
    assert np.isnan(iv).all()


def test_implied_forward_and_carry():
    S, T, r, q = 100.0, 0.5, 0.04, 0.015
    K = np.arange(80, 121, 5.0)
    c = bs_price(S, K, T, r, q, 0.25, True)
    p = bs_price(S, K, T, r, q, 0.25, False)
    F = implied_forward(K, c, p, T, r)
    assert F == pytest.approx(S * np.exp((r - q) * T), rel=1e-10)
    assert implied_carry(F, S, T, r) == pytest.approx(q, abs=1e-10)
    assert np.isnan(implied_forward(K, c * np.nan, p, T, r))
