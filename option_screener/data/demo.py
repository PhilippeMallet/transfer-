"""Source de démonstration : sous-jacent simulé + chaîne d'options générée.

Sert à tester l'outil sans connexion. Le scénario est construit pour montrer
les trois verdicts :
- la vol réalisée récente est élevée (le GARCH prévoit un retour vers la
  moyenne), donc la vol juste baisse avec la maturité ;
- les échéances courtes cotent au-dessus de la vol juste (=> « Cher » ou
  « Neutre »), celles à 2-3 mois en dessous (=> « Bon marché ») ;
- une publication de résultats tombe dans 46 jours : les échéances qui
  l'incluent sont gonflées par l'événement (mouvement de 4 %) ;
- skew classique : puts OTM plus chers que les calls OTM.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..pricing import option_price
from ..volatility import FairVolModel
from .base import DataProvider, MarketData, normalize_chain


def simulate_history(n_days=504, spot_end=100.0, sigma_long=0.22, sigma_recent=0.32,
                     recent_days=25, seed=7, end=None) -> pd.DataFrame:
    """OHLC quotidien simulé (78 pas de 5 min + gap d'ouverture)."""
    rng = np.random.default_rng(seed)
    end = pd.Timestamp(end or dt.date.today()).normalize()
    dates = pd.bdate_range(end=end - pd.Timedelta(days=1), periods=n_days)
    sig = np.full(n_days, sigma_long)
    sig[-recent_days:] = sigma_recent
    daily = sig / np.sqrt(252)
    steps = 78  # pas de 5 minutes : plus hauts / plus bas réalistes
    overnight_share = 0.2
    rows = []
    price = 1.0
    for d_sig in daily:
        gap = rng.normal(0, d_sig * np.sqrt(overnight_share))
        o = price * np.exp(gap)
        incr = rng.normal(0, d_sig * np.sqrt((1 - overnight_share) / steps), steps)
        path = o * np.exp(np.cumsum(incr))
        h = max(o, path.max())
        lo = min(o, path.min())
        c = path[-1]
        rows.append((o, h, lo, c))
        price = c
    df = pd.DataFrame(rows, index=dates, columns=["open", "high", "low", "close"])
    return df * (spot_end / df["close"].iloc[-1])


def _expiries(today: dt.date, max_days: int = 270) -> list[dt.date]:
    out = []
    d = today + dt.timedelta(days=(4 - today.weekday()) % 7 or 7)  # prochain vendredi
    while (d - today).days <= 56:
        out.append(d)
        d += dt.timedelta(days=7)
    month = dt.date(today.year, today.month, 1)
    while True:
        month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        first_friday = month + dt.timedelta(days=(4 - month.weekday()) % 7)
        third = first_friday + dt.timedelta(days=14)
        if (third - today).days > max_days:
            break
        if third not in out and (third - today).days > 56:
            out.append(third)
    return sorted(out)


def make_demo_market(now: dt.datetime | None = None, spot: float = 100.0, r: float = 0.04,
                     q: float = 0.01, seed: int = 7, event_move: float = 0.04,
                     earnings_in_days: int = 46, style: str = "american") -> MarketData:
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    history = simulate_history(spot_end=spot, seed=seed, end=today)
    fair = FairVolModel(history, "garch")
    earnings = today + dt.timedelta(days=earnings_in_days)
    rng = np.random.default_rng(seed + 1)

    rows = []
    for exp in _expiries(today):
        days = (exp - today).days
        T = days / 365.0
        base = fair(T)
        # prime de marché : riche à court terme, décotée au-delà de ~6 semaines
        premium = max(0.05 - 0.45 * T, -0.06)
        atm_var = (base + premium) ** 2
        if exp > earnings:
            atm_var += event_move**2 / T        # variance de l'événement étalée sur T
        atm = np.sqrt(atm_var)
        F = spot * np.exp((r - q) * T)
        step = 1.0 if days <= 21 else 2.5 if days <= 90 else 5.0
        width = max(0.12, 2.5 * atm * np.sqrt(T))
        strikes = np.arange(np.floor(spot * (1 - width) / step) * step,
                            spot * (1 + width) + step, step)
        k = np.log(strikes / F)
        smile = atm - 0.25 * k * np.sqrt(0.25 / max(T, 1 / 52)) + 0.6 * k**2
        smile = np.clip(smile, 0.05, 2.0)
        for is_call in (True, False):
            prices = option_price(spot, strikes, T, r, q, smile, is_call,
                                  american=(style == "american"), steps=150)
            half = np.clip(0.03 * prices, 0.01, 0.15)
            bid = np.floor((prices - half) * 100) / 100
            ask = np.ceil((prices + half) * 100) / 100
            bid = np.where(bid < 0.01, 0.0, bid)
            for K, b, a, p, iv in zip(strikes, bid, ask, prices, smile):
                if a < 0.03:
                    continue
                moneyness = abs(np.log(K / spot))
                liq = np.exp(-moneyness * 8)
                rows.append({
                    "expiry": exp, "strike": float(K), "type": "C" if is_call else "P",
                    "bid": float(b), "ask": float(a), "last": float(round(p, 2)),
                    "volume": int(rng.poisson(400 * liq)),
                    "open_interest": int(rng.poisson(3000 * liq)),
                    "vendor_iv": float(iv), "style": style, "multiplier": 100.0,
                })
    chain = normalize_chain(pd.DataFrame(rows), style)
    md = MarketData(
        ticker="DEMO", spot=spot, chain=chain, history=history, rate=r, div_yield=q,
        iv_history=_demo_iv_history(history), earnings_date=earnings, style=style,
        source="Démo", asof=now,
        notes=["Données simulées : sous-jacent fictif, à utiliser pour découvrir l'outil."],
    )
    return md


def _demo_iv_history(history: pd.DataFrame) -> pd.Series:
    """IV ATM 30 j fictive : vol réalisée 20 j + prime, lissée."""
    rv = np.log(history["close"]).diff().rolling(20).std() * np.sqrt(252)
    return (rv.ewm(span=10).mean() + 0.02).dropna().iloc[-252:]


class DemoProvider(DataProvider):
    name = "Démo"

    def load(self, ticker: str = "DEMO", dte_min: int = 0, dte_max: int = 270,
             moneyness: float = 0.30, years: int = 2) -> MarketData:
        md = make_demo_market()
        exp_days = pd.Series([(e - md.asof.date()).days for e in md.chain["expiry"]])
        md.chain = md.chain[(exp_days >= dte_min).values & (exp_days <= dte_max).values]
        md.chain = md.chain.reset_index(drop=True)
        return md
