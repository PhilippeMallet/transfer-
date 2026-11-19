"""Modèles de pricing : Black-Scholes-Merton, arbre binomial CRR (exercice
américain) et solveur de volatilité implicite vectorisé.

Conventions :
- ``T`` en années, ``r`` et ``q`` en taux continus annualisés, ``sigma`` en
  décimal (0.20 = 20 %).
- ``is_call`` : booléen (ou tableau de booléens).
- Toutes les fonctions acceptent des scalaires ou des tableaux numpy
  (broadcast) et renvoient des tableaux numpy.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

SIGMA_MIN = 0.01  # 1 % : en dessous, l'arbre binomial dégénère (et aucune option réelle ne cote là)
SIGMA_MAX = 5.0


def _arr(*xs):
    out = np.broadcast_arrays(*[np.asarray(x, dtype=float) for x in xs])
    return [np.array(o, dtype=float) for o in out]


# ---------------------------------------------------------------------------
# Black-Scholes-Merton
# ---------------------------------------------------------------------------

def _d1_d2(S, K, T, r, q, sigma):
    sqrt_t = np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return d1, d2


def bs_price(S, K, T, r, q, sigma, is_call):
    """Prix Black-Scholes-Merton (européen) avec rendement de dividende q."""
    S, K, T, r, q, sigma = _arr(S, K, T, r, q, sigma)
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), S.shape)
    T_safe = np.maximum(T, 1e-12)
    sig = np.maximum(sigma, 1e-12)
    d1, d2 = _d1_d2(S, K, T_safe, r, q, sig)
    df_q = np.exp(-q * T_safe)
    df_r = np.exp(-r * T_safe)
    call = S * df_q * norm.cdf(d1) - K * df_r * norm.cdf(d2)
    put = K * df_r * norm.cdf(-d2) - S * df_q * norm.cdf(-d1)
    price = np.where(is_call, call, put)
    intrinsic = np.where(is_call, np.maximum(S - K, 0.0), np.maximum(K - S, 0.0))
    return np.where(T <= 0, intrinsic, price)


def bs_vega(S, K, T, r, q, sigma):
    """Vega BSM pour 1.00 de vol (diviser par 100 pour 1 point de vol)."""
    S, K, T, r, q, sigma = _arr(S, K, T, r, q, sigma)
    T_safe = np.maximum(T, 1e-12)
    d1, _ = _d1_d2(S, K, T_safe, r, q, np.maximum(sigma, 1e-12))
    return S * np.exp(-q * T_safe) * norm.pdf(d1) * np.sqrt(T_safe)


def bs_greeks(S, K, T, r, q, sigma, is_call):
    """Grecques BSM.

    Renvoie un dict : delta, gamma, vega (par point de vol), theta (par jour
    calendaire). Pour les options américaines c'est une approximation,
    suffisante pour un screener.
    """
    S, K, T, r, q, sigma = _arr(S, K, T, r, q, sigma)
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), S.shape)
    T_safe = np.maximum(T, 1e-12)
    sig = np.maximum(sigma, 1e-12)
    d1, d2 = _d1_d2(S, K, T_safe, r, q, sig)
    df_q = np.exp(-q * T_safe)
    df_r = np.exp(-r * T_safe)
    pdf = norm.pdf(d1)
    delta = np.where(is_call, df_q * norm.cdf(d1), -df_q * norm.cdf(-d1))
    gamma = df_q * pdf / (S * sig * np.sqrt(T_safe))
    vega = S * df_q * pdf * np.sqrt(T_safe) / 100.0
    common = -S * df_q * pdf * sig / (2 * np.sqrt(T_safe))
    theta_call = common - r * K * df_r * norm.cdf(d2) + q * S * df_q * norm.cdf(d1)
    theta_put = common + r * K * df_r * norm.cdf(-d2) - q * S * df_q * norm.cdf(-d1)
    theta = np.where(is_call, theta_call, theta_put) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


# ---------------------------------------------------------------------------
# Arbre binomial CRR (vectorisé sur plusieurs options)
# ---------------------------------------------------------------------------

def binomial_price(S, K, T, r, q, sigma, is_call, american=True, steps=200,
                   control_variate=True):
    """Prix par arbre binomial Cox-Ross-Rubinstein.

    Vectorisé : chaque élément des tableaux est une option, toutes les options
    sont propagées dans l'arbre en même temps.

    ``control_variate`` : on calcule aussi l'européenne sur le même arbre et on
    corrige par l'erreur de l'arbre vs Black-Scholes (technique de variable de
    contrôle, Hull). Ça supprime l'essentiel de l'oscillation de l'arbre CRR.
    """
    S, K, T, r, q, sigma = _arr(S, K, T, r, q, sigma)
    shape = S.shape
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), shape).ravel()
    S, K, T, r, q, sigma = (x.ravel() for x in (S, K, T, r, q, sigma))
    n = steps

    T_safe = np.maximum(T, 1e-8)
    sig = np.clip(sigma, SIGMA_MIN, SIGMA_MAX)
    dt = T_safe / n
    log_u = sig * np.sqrt(dt)
    u = np.exp(log_u)
    d = 1.0 / u
    disc = np.exp(-r * dt)
    p = np.clip((np.exp((r - q) * dt) - d) / (u - d), 0.0, 1.0)

    j = np.arange(n + 1)
    sign = np.where(is_call, 1.0, -1.0)[:, None]
    ST = S[:, None] * np.exp(log_u[:, None] * (2 * j[None, :] - n))
    v_am = np.maximum(sign * (ST - K[:, None]), 0.0)
    v_eu = v_am.copy()
    p_ = p[:, None]
    disc_ = disc[:, None]
    for i in range(n - 1, -1, -1):
        v_eu = disc_ * (p_ * v_eu[:, 1:i + 2] + (1 - p_) * v_eu[:, 0:i + 1])
        cont = disc_ * (p_ * v_am[:, 1:i + 2] + (1 - p_) * v_am[:, 0:i + 1])
        if american:
            Si = S[:, None] * np.exp(log_u[:, None] * (2 * j[None, :i + 1] - i))
            v_am = np.maximum(cont, sign * (Si - K[:, None]))
        else:
            v_am = cont
    am = v_am[:, 0]
    eu_tree = v_eu[:, 0]
    if control_variate:
        eu_bs = bs_price(S, K, T_safe, r, q, sig, is_call)
        am = am - eu_tree + eu_bs
        if american:
            intrinsic = np.maximum(sign[:, 0] * (S - K), 0.0)
            am = np.maximum(am, np.maximum(intrinsic, eu_bs))
    intrinsic = np.maximum(sign[:, 0] * (S - K), 0.0)
    am = np.where(T <= 0, intrinsic, am)
    return am.reshape(shape)


def option_price(S, K, T, r, q, sigma, is_call, american=False, steps=200):
    """Prix d'option : BSM pour l'européen, binomial pour l'américain.

    Un call américain sans dividende (q <= 0) vaut le call européen : on
    utilise alors BSM directement (plus rapide et exact).
    """
    S, K, T, r, q, sigma = _arr(S, K, T, r, q, sigma)
    shape = S.shape
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), shape)
    american = np.broadcast_to(np.asarray(american, dtype=bool), shape)
    out = bs_price(S, K, T, r, q, sigma, is_call)
    need_tree = american & ~(is_call & (q <= 0)) & (T > 0)
    if np.any(need_tree):
        m = need_tree
        out = np.array(out, dtype=float)
        out[m] = binomial_price(S[m], K[m], T[m], r[m], q[m], sigma[m],
                                is_call[m], american=True, steps=steps)
    return out


# ---------------------------------------------------------------------------
# Volatilité implicite
# ---------------------------------------------------------------------------

def price_bounds(S, K, T, r, q, is_call, american=False):
    """Bornes d'arbitrage (min, max) du prix d'une option."""
    S, K, T, r, q = _arr(S, K, T, r, q)
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), S.shape)
    american = np.broadcast_to(np.asarray(american, dtype=bool), S.shape)
    fwd_s = S * np.exp(-q * T)
    pv_k = K * np.exp(-r * T)
    lower_eu = np.where(is_call, np.maximum(fwd_s - pv_k, 0.0), np.maximum(pv_k - fwd_s, 0.0))
    intrinsic = np.where(is_call, np.maximum(S - K, 0.0), np.maximum(K - S, 0.0))
    lower = np.where(american, np.maximum(lower_eu, intrinsic), lower_eu)
    upper = np.where(is_call, np.where(american, S, fwd_s), np.where(american, K, pv_k))
    return lower, upper


def implied_vol(price, S, K, T, r, q, is_call, american=False, steps=150,
                tol=1e-6, max_iter=60):
    """Volatilité implicite vectorisée.

    Newton-Raphson sécurisé par bissection : on garde un encadrement
    [lo, hi] et on n'accepte le pas de Newton que s'il reste dedans, sinon on
    bissecte. Pour l'américain, la dérivée utilisée est la vega BSM (très
    proche de la vraie), le prix est celui de l'arbre binomial.

    Renvoie NaN quand le prix est hors des bornes d'arbitrage, nul, ou que
    la maturité est échue.
    """
    price, S, K, T, r, q = _arr(price, S, K, T, r, q)
    shape = price.shape
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), shape).ravel()
    american = np.broadcast_to(np.asarray(american, dtype=bool), shape).ravel()
    price, S, K, T, r, q = (x.ravel() for x in (price, S, K, T, r, q))

    result = np.full(price.shape, np.nan)
    lower, upper = price_bounds(S, K, T, r, q, is_call, american)
    valid = (
        np.isfinite(price) & (price > 0) & (T > 0) & np.isfinite(S) & np.isfinite(K)
        & (price > lower + 1e-8) & (price < upper - 1e-8)
    )
    if not np.any(valid):
        return result.reshape(shape)

    idx = np.where(valid)[0]
    P, s_, k_, t_, r_, q_ = price[idx], S[idx], K[idx], T[idx], r[idx], q[idx]
    c_, a_ = is_call[idx], american[idx]

    def pricer(sig, m):
        return option_price(s_[m], k_[m], t_[m], r_[m], q_[m], sig, c_[m], a_[m], steps=steps)

    everything = np.ones(idx.shape, dtype=bool)
    lo = np.full(idx.shape, SIGMA_MIN)
    hi = np.full(idx.shape, SIGMA_MAX)
    p_lo, p_hi = pricer(lo, everything), pricer(hi, everything)
    in_range = (P >= p_lo - 1e-10) & (P <= p_hi + 1e-10)

    # point de départ : approximation de Brenner-Subrahmanyam bornée
    sigma = np.clip(np.sqrt(2 * np.pi / t_) * P / s_, 0.05, 1.5)
    done = ~in_range
    for _ in range(max_iter):
        m = ~done
        if not np.any(m):
            break
        sig_m = sigma[m]
        diff = pricer(sig_m, m) - P[m]
        hi[m] = np.where(diff > 0, sig_m, hi[m])
        lo[m] = np.where(diff < 0, sig_m, lo[m])
        vega = bs_vega(s_[m], k_[m], t_[m], r_[m], q_[m], sig_m)
        conv = (np.abs(diff) <= np.maximum(vega * tol, 1e-10)) | ((hi[m] - lo[m]) < tol)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            newton = sig_m - diff / vega
        ok = np.isfinite(newton) & (newton > lo[m]) & (newton < hi[m]) & (vega > 1e-10)
        new_sigma = np.where(ok, newton, 0.5 * (lo[m] + hi[m]))
        sigma[m] = np.where(conv, sig_m, new_sigma)
        done[np.where(m)[0][conv]] = True

    sigma = np.where(in_range, sigma, np.nan)
    result[idx] = sigma
    return result.reshape(shape)


# ---------------------------------------------------------------------------
# Forward implicite (parité call-put)
# ---------------------------------------------------------------------------

def implied_forward(strikes, call_mid, put_mid, T, r, n_strikes=3):
    """Forward implicite d'une échéance par parité call-put.

    F = K + e^{rT} (C - P), calculé sur les ``n_strikes`` strikes où |C - P|
    est le plus petit (les plus proches de l'ATM), puis médiane.
    Renvoie NaN si pas assez de paires valides.
    """
    K = np.asarray(strikes, dtype=float)
    C = np.asarray(call_mid, dtype=float)
    P = np.asarray(put_mid, dtype=float)
    ok = np.isfinite(K) & np.isfinite(C) & np.isfinite(P) & (C > 0) & (P > 0)
    if ok.sum() < 1 or T <= 0:
        return float("nan")
    K, C, P = K[ok], C[ok], P[ok]
    order = np.argsort(np.abs(C - P))[:n_strikes]
    fwd = K[order] + np.exp(r * T) * (C[order] - P[order])
    return float(np.median(fwd))


def implied_carry(forward, spot, T, r):
    """q implicite tel que F = S e^{(r - q) T}."""
    if not (np.isfinite(forward) and forward > 0 and spot > 0 and T > 0):
        return float("nan")
    return float(r - np.log(forward / spot) / T)
