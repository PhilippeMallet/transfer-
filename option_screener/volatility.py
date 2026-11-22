"""Volatilité réalisée, prévisions (EWMA, GARCH) et cône de vol.

L'historique attendu est un DataFrame indexé par date avec les colonnes
``open``, ``high``, ``low``, ``close`` (minuscules).
Toutes les vols sont annualisées sur 252 jours de bourse.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import lfilter

TRADING_DAYS = 252


def log_returns(history: pd.DataFrame) -> pd.Series:
    return np.log(history["close"]).diff().dropna()


# ---------------------------------------------------------------------------
# Estimateurs de vol réalisée (séries glissantes)
#
# Parkinson et Garman-Klass n'utilisent que la séance (plus haut / plus bas /
# ouverture / clôture) : ils ignorent les gaps d'ouverture et sous-estiment la
# vol d'un titre qui bouge beaucoup la nuit (résultats publiés hors séance...).
# Yang-Zhang combine gap + séance : c'est l'estimateur par défaut.
# ---------------------------------------------------------------------------

def close_to_close(history: pd.DataFrame, window: int) -> pd.Series:
    r = np.log(history["close"]).diff()
    return r.rolling(window).std() * np.sqrt(TRADING_DAYS)


def parkinson(history: pd.DataFrame, window: int) -> pd.Series:
    hl = np.log(history["high"] / history["low"]) ** 2
    return np.sqrt(hl.rolling(window).mean() / (4 * np.log(2)) * TRADING_DAYS)


def garman_klass(history: pd.DataFrame, window: int) -> pd.Series:
    hl = np.log(history["high"] / history["low"]) ** 2
    co = np.log(history["close"] / history["open"]) ** 2
    var = 0.5 * hl - (2 * np.log(2) - 1) * co
    return np.sqrt(var.rolling(window).mean().clip(lower=0) * TRADING_DAYS)


def yang_zhang(history: pd.DataFrame, window: int) -> pd.Series:
    o = np.log(history["open"] / history["close"].shift(1))
    c = np.log(history["close"] / history["open"])
    h = np.log(history["high"] / history["open"])
    lo = np.log(history["low"] / history["open"])
    rs = h * (h - c) + lo * (lo - c)
    n = window
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = o.rolling(n).var() + k * c.rolling(n).var() + (1 - k) * rs.rolling(n).mean()
    return np.sqrt(var.clip(lower=0) * TRADING_DAYS)


ESTIMATORS = {
    "close-to-close": close_to_close,
    "parkinson": parkinson,
    "garman-klass": garman_klass,
    "yang-zhang": yang_zhang,
}


def realized_vol_table(history: pd.DataFrame, windows=(10, 20, 30, 60, 90)) -> pd.DataFrame:
    """Dernière valeur de chaque estimateur pour chaque fenêtre."""
    rows = {}
    for name, fn in ESTIMATORS.items():
        rows[name] = {w: _last(fn(history, w)) for w in windows if len(history) > w + 1}
    return pd.DataFrame(rows).T


def realized_vol(history: pd.DataFrame, window: int, method: str = "yang-zhang") -> float:
    window = int(max(5, min(window, len(history) - 2)))
    return _last(ESTIMATORS[method](history, window))


def _last(series: pd.Series) -> float:
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else float("nan")


# ---------------------------------------------------------------------------
# EWMA (RiskMetrics)
# ---------------------------------------------------------------------------

def ewma_vol(history: pd.DataFrame, lam: float = 0.94) -> float:
    r = log_returns(history).values
    if len(r) < 10:
        return float("nan")
    var = np.var(r[:20]) if len(r) >= 20 else np.var(r)
    for x in r:
        var = lam * var + (1 - lam) * x * x
    return float(np.sqrt(var * TRADING_DAYS))


# ---------------------------------------------------------------------------
# GARCH(1,1)
# ---------------------------------------------------------------------------

@dataclass
class GarchModel:
    omega: float          # en variance quotidienne
    alpha: float
    beta: float
    last_var: float       # variance conditionnelle prévue pour demain
    n_obs: int

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def long_run_vol(self) -> float:
        p = self.persistence
        if p >= 1:
            return float("nan")
        return float(np.sqrt(self.omega / (1 - p) * TRADING_DAYS))

    @property
    def half_life_days(self) -> float:
        p = self.persistence
        if not 0 < p < 1:
            return float("nan")
        return float(np.log(0.5) / np.log(p))

    def term_vol(self, horizon_days: float) -> float:
        """Vol moyenne annualisée prévue sur les ``horizon_days`` prochains
        jours de bourse : racine de la moyenne des variances attendues."""
        h = max(1, int(round(horizon_days)))
        p = self.persistence
        if p >= 1:
            return float(np.sqrt(self.last_var * TRADING_DAYS))
        vl = self.omega / (1 - p)
        k = np.arange(h)
        exp_var = vl + p**k * (self.last_var - vl)
        return float(np.sqrt(exp_var.mean() * TRADING_DAYS))


def _garch_filter(params, r):
    """var[t] = omega + alpha r[t-1]^2 + beta var[t-1], var[0] = var(r).

    Récurrence linéaire en var : calculée avec lfilter (rapide)."""
    omega, alpha, beta = params
    v0 = np.var(r)
    x = omega + alpha * r[:-1] ** 2
    tail, _ = lfilter([1.0], [1.0, -beta], x, zi=[beta * v0])
    return np.concatenate(([v0], tail))


def _garch_nll(params, r):
    omega, alpha, beta = params
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.9995:
        return 1e12
    var = _garch_filter(params, r)
    if np.any(var <= 0):
        return 1e12
    return 0.5 * np.sum(np.log(var) + r**2 / var)


def fit_garch(history: pd.DataFrame, max_obs: int = 750) -> GarchModel | None:
    """GARCH(1,1) gaussien estimé par maximum de vraisemblance.

    Les rendements sont centrés et multipliés par 100 pour la stabilité
    numérique ; les paramètres renvoyés sont remis à l'échelle décimale.
    """
    r = log_returns(history).values[-max_obs:]
    if len(r) < 100:
        return None
    r = (r - r.mean()) * 100.0
    var0 = np.var(r)
    best = None
    for a0, b0 in ((0.05, 0.90), (0.10, 0.85), (0.03, 0.95)):
        x0 = np.array([var0 * (1 - a0 - b0), a0, b0])
        res = minimize(_garch_nll, x0, args=(r,), method="Nelder-Mead",
                       options={"xatol": 1e-8, "fatol": 1e-8, "maxiter": 4000})
        if best is None or res.fun < best.fun:
            best = res
    omega, alpha, beta = best.x
    if not (omega > 0 and alpha >= 0 and beta >= 0 and alpha + beta < 1):
        return None
    var = _garch_filter(best.x, r)
    next_var = omega + alpha * r[-1] ** 2 + beta * var[-1]
    scale = 100.0**2
    return GarchModel(omega=omega / scale, alpha=float(alpha), beta=float(beta),
                      last_var=next_var / scale, n_obs=len(r))


# ---------------------------------------------------------------------------
# Vol « juste » par horizon
# ---------------------------------------------------------------------------

FAIR_VOL_MODELS = ("garch", "ewma", "yang-zhang")


class FairVolModel:
    """Donne la vol juste pour une maturité, selon le modèle choisi.

    - ``garch`` : prévision GARCH(1,1) de la vol moyenne jusqu'à l'échéance
      (retombe sur Yang-Zhang si le GARCH ne converge pas) ;
    - ``ewma`` : vol EWMA actuelle, plate sur toutes les maturités ;
    - ``yang-zhang`` : vol réalisée Yang-Zhang sur une fenêtre ≈ durée de vie
      de l'option (min 10 jours).
    - ``manual`` : valeur fixe fournie par l'utilisateur.
    """

    def __init__(self, history: pd.DataFrame | None, model: str = "garch",
                 manual_vol: float | None = None):
        self.history = history
        self.model = model
        self.manual_vol = manual_vol
        self.garch = None
        self.ewma = float("nan")
        self.notes: list[str] = []
        if model == "manual":
            if manual_vol is None:
                raise ValueError("Vol juste manuelle non renseignée")
            return
        if history is None or len(history) < 30:
            raise ValueError("Historique insuffisant pour estimer la vol juste "
                             "(il faut au moins 30 séances) : utilise une vol manuelle.")
        self.ewma = ewma_vol(history)
        if model == "garch":
            self.garch = fit_garch(history)
            if self.garch is None:
                self.notes.append("GARCH non convergé ou historique trop court : "
                                  "repli sur la vol réalisée Yang-Zhang.")

    def __call__(self, T_years: float) -> float:
        days = max(1.0, T_years * TRADING_DAYS)
        if self.model == "manual":
            return float(self.manual_vol)
        if self.model == "ewma":
            return self.ewma
        if self.model == "garch" and self.garch is not None:
            return self.garch.term_vol(days)
        return realized_vol(self.history, max(10, int(round(days))), "yang-zhang")

    def label(self) -> str:
        return {"garch": "GARCH(1,1)", "ewma": "EWMA 0,94", "yang-zhang": "Yang-Zhang",
                "manual": "Manuelle"}.get(self.model, self.model)


# ---------------------------------------------------------------------------
# Cône de vol, IV rank, mouvements historiques
# ---------------------------------------------------------------------------

def vol_cone(history: pd.DataFrame, windows=(10, 20, 30, 60, 90, 120),
             lookback: int = 504, method: str = "close-to-close") -> pd.DataFrame:
    """Percentiles de la vol réalisée glissante pour chaque fenêtre."""
    fn = ESTIMATORS[method]
    hist = history.iloc[-(lookback + max(windows) + 1):]
    rows = []
    for w in windows:
        if len(hist) <= w + 5:
            continue
        s = fn(hist, w).dropna().iloc[-lookback:]
        if s.empty:
            continue
        rows.append({
            "fenetre": w,
            "min": s.min(), "p25": s.quantile(0.25), "mediane": s.median(),
            "p75": s.quantile(0.75), "max": s.max(), "actuelle": s.iloc[-1],
        })
    return pd.DataFrame(rows)


def cone_percentile(history: pd.DataFrame, vol: float, horizon_days: float,
                    lookback: int = 504) -> float:
    """Percentile (0-100) d'une vol dans la distribution historique de la vol
    réalisée close-to-close sur une fenêtre égale à l'horizon."""
    if history is None or not np.isfinite(vol):
        return float("nan")
    w = int(max(5, min(round(horizon_days), len(history) // 3)))
    s = close_to_close(history.iloc[-(lookback + w + 1):], w).dropna().iloc[-lookback:]
    if len(s) < 20:
        return float("nan")
    return float((s < vol).mean() * 100)


def iv_rank(iv_history: pd.Series | None, current: float, lookback: int = 252):
    """IV rank et IV percentile sur ``lookback`` séances.

    - IV rank = (IV actuelle - min) / (max - min)
    - IV percentile = % des séances où l'IV était plus basse qu'aujourd'hui
    Renvoie (nan, nan) si pas d'historique.
    """
    if iv_history is None or not np.isfinite(current):
        return float("nan"), float("nan")
    s = pd.Series(iv_history).dropna().iloc[-lookback:]
    if len(s) < 20:
        return float("nan"), float("nan")
    lo, hi = s.min(), s.max()
    rank = (current - lo) / (hi - lo) * 100 if hi > lo else float("nan")
    pct = (s < current).mean() * 100
    return float(np.clip(rank, 0, 100)), float(pct)


def historical_abs_move(history: pd.DataFrame, horizon_days: float, lookback: int = 504) -> float:
    """Mouvement absolu moyen (en %) du sous-jacent sur ``horizon_days`` séances,
    calculé sur des fenêtres glissantes de l'historique."""
    h = max(1, int(round(horizon_days)))
    c = np.log(history["close"].iloc[-(lookback + h + 1):])
    moves = (c.shift(-h) - c).dropna()
    if len(moves) < 20:
        return float("nan")
    return float(np.mean(np.abs(np.expm1(moves))))
