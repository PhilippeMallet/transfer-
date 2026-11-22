import numpy as np
import pandas as pd
import pytest

from option_screener.data.demo import simulate_history
from option_screener.volatility import (ESTIMATORS, FairVolModel, GarchModel, ewma_vol, fit_garch,
                                        historical_abs_move, iv_rank, realized_vol, vol_cone)


@pytest.fixture(scope="module")
def gbm_history():
    return simulate_history(n_days=1500, sigma_long=0.25, sigma_recent=0.25, seed=3)


def test_realized_estimators_recover_sigma(gbm_history):
    # La simulation met 20 % de la variance dans le gap d'ouverture. Close-to-close et
    # Yang-Zhang le captent ; Parkinson et Garman-Klass ne voient que la séance (et la
    # discrétisation en 78 pas raccourcit un peu les plus hauts / plus bas).
    intraday = 0.25 * np.sqrt(0.8)
    expected = {"close-to-close": (0.25, 0.03), "yang-zhang": (0.25, 0.035),
                "parkinson": (intraday, 0.045), "garman-klass": (intraday, 0.045)}
    for name, fn in ESTIMATORS.items():
        est = fn(gbm_history, 1000).dropna().iloc[-1]
        target, tol = expected[name]
        assert est == pytest.approx(target, abs=tol), name


def test_realized_vol_window_is_clamped(gbm_history):
    short = gbm_history.iloc[:40]
    assert np.isfinite(realized_vol(short, 500))


def test_ewma_close_to_sigma(gbm_history):
    assert ewma_vol(gbm_history) == pytest.approx(0.25, abs=0.08)


def _simulate_garch(n, omega, alpha, beta, seed=0):
    rng = np.random.default_rng(seed)
    var = omega / (1 - alpha - beta)
    r = np.empty(n)
    for t in range(n):
        r[t] = np.sqrt(var) * rng.standard_normal()
        var = omega + alpha * r[t] ** 2 + beta * var
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)


def test_garch_recovers_parameters():
    omega, alpha, beta = 2e-6, 0.08, 0.90
    hist = _simulate_garch(4000, omega, alpha, beta, seed=11)
    g = fit_garch(hist, max_obs=4000)
    assert g is not None
    assert g.persistence == pytest.approx(alpha + beta, abs=0.03)
    true_lr = np.sqrt(omega / (1 - alpha - beta) * 252)
    assert g.long_run_vol == pytest.approx(true_lr, rel=0.2)


def test_garch_term_structure_mean_reverts():
    g = GarchModel(omega=0.25**2 / 252 * 0.02, alpha=0.08, beta=0.90, last_var=0.40**2 / 252, n_obs=500)
    assert g.term_vol(1) == pytest.approx(0.40, rel=1e-9)
    assert g.term_vol(10_000) == pytest.approx(g.long_run_vol, rel=0.02)
    vols = [g.term_vol(h) for h in (5, 20, 60, 250)]
    assert vols == sorted(vols, reverse=True)


def test_fair_vol_models(gbm_history):
    for model in ("garch", "ewma", "yang-zhang"):
        v = FairVolModel(gbm_history, model)(30 / 365)
        assert 0.1 < v < 0.5, model
    assert FairVolModel(None, "manual", 0.33)(0.2) == 0.33
    with pytest.raises(ValueError):
        FairVolModel(gbm_history.iloc[:10], "garch")


def test_iv_rank_and_percentile():
    s = pd.Series(np.linspace(0.10, 0.30, 252))
    rank, pct = iv_rank(s, 0.20)
    assert rank == pytest.approx(50, abs=0.5)
    assert pct == pytest.approx(50, abs=1)
    assert all(np.isnan(x) for x in iv_rank(None, 0.2))


def test_vol_cone_and_moves(gbm_history):
    cone = vol_cone(gbm_history)
    assert list(cone.columns) == ["fenetre", "min", "p25", "mediane", "p75", "max", "actuelle"]
    assert (cone["min"] <= cone["mediane"]).all() and (cone["mediane"] <= cone["max"]).all()
    move = historical_abs_move(gbm_history, 21)
    # E|N(0, s)| = s * sqrt(2/pi), s = 0,25 * sqrt(21/252)
    assert move == pytest.approx(0.25 * np.sqrt(21 / 252) * np.sqrt(2 / np.pi), rel=0.25)
