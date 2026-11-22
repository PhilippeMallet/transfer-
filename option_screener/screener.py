"""Moteur du screener : vol implicite de chaque option vs vol « juste ».

Entrée : un ``MarketData`` (chaîne + contexte) et des ``ScreenParams``.
Sortie : un ``ScreenResult`` avec le tableau des options classées, la
structure par terme, le résumé et le cône de vol.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .data.base import MarketData
from .pricing import bs_greeks, implied_carry, implied_forward, implied_vol, option_price
from .volatility import (FairVolModel, close_to_close, cone_percentile, historical_abs_move,
                         iv_rank, realized_vol, vol_cone)

VERDICT_CHEAP = "Bon marché"
VERDICT_NEUTRAL = "Neutre"
VERDICT_RICH = "Cher"
DEFAULT_RATE = 0.04


@dataclass
class ScreenParams:
    option_type: str = "both"            # "C", "P" ou "both"
    dte_min: int = 7
    dte_max: int = 120
    delta_min: float = 0.15              # |delta|
    delta_max: float = 0.85
    min_open_interest: float = 0
    min_volume: float = 0
    max_spread_pct: float = 0.50         # (ask - bid) / mid
    fair_vol_model: str = "garch"        # garch | ewma | yang-zhang | manual
    manual_fair_vol: float | None = None
    rich_threshold: float = 3.0          # points de vol au-dessus de la vol juste
    cheap_threshold: float = -1.0        # points de vol en dessous
    rate_override: float | None = None
    div_override: float | None = None
    style_override: str | None = None    # "american" | "european" | None
    earnings_move: float = 0.0           # mouvement attendu aux résultats (0.05 = 5 %), ajouté à la vol juste
    vendor_iv_tolerance: float = 1.0     # points
    wide_spread_pts: float = 2.0         # alerte si demi-spread > x points de vol
    binomial_steps: int = 150


@dataclass
class ScreenResult:
    options: pd.DataFrame                # options retenues, triées (moins chères d'abord)
    full: pd.DataFrame                   # toutes les options de la plage DTE (pour le smile)
    term: pd.DataFrame                   # une ligne par échéance
    summary: dict
    cone: pd.DataFrame
    fair_model: FairVolModel
    notes: list[str] = field(default_factory=list)


def year_fraction(expiry: dt.date, now: dt.datetime, tz: str = "America/New_York",
                  hour: int = 16) -> float:
    close = dt.datetime.combine(expiry, dt.time(hour), tzinfo=ZoneInfo(tz))
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return max((close - now).total_seconds(), 0.0) / (365.0 * 86400.0)


def _verdict(edge_pts, rich, cheap):
    return np.where(np.isnan(edge_pts), "N/A",
                    np.where(edge_pts > rich, VERDICT_RICH,
                             np.where(edge_pts < cheap, VERDICT_CHEAP, VERDICT_NEUTRAL)))


def _atm_iv(g: pd.DataFrame, F: float) -> float:
    """IV ATM d'une échéance : interpolation au forward sur les options OTM
    (puts sous le forward, calls au-dessus), en log-moneyness."""
    otm = g[((g["type"] == "C") & (g["strike"] >= F)) | ((g["type"] == "P") & (g["strike"] < F))]
    otm = otm[np.isfinite(otm["iv_mid"])].sort_values("strike")
    if otm.empty:
        alt = g[np.isfinite(g["iv_mid"])]
        if alt.empty:
            return float("nan")
        return float(alt.iloc[(alt["strike"] - F).abs().argsort()[:2]]["iv_mid"].mean())
    x = np.log(otm["strike"].values / F)
    return float(np.interp(0.0, x, otm["iv_mid"].values))


def _interp_term(term: pd.DataFrame, col: str, days: float) -> float:
    """Interpolation en variance totale d'une colonne de vol à ``days`` jours."""
    t = term[np.isfinite(term[col]) & (term["T"] > 0)].sort_values("T")
    if t.empty:
        return float("nan")
    T = days / 365.0
    w = t[col].values ** 2 * t["T"].values
    if T <= t["T"].iloc[0]:
        return float(t[col].iloc[0])
    if T >= t["T"].iloc[-1]:
        return float(t[col].iloc[-1])
    return float(np.sqrt(np.interp(T, t["T"].values, w) / T))


def analyze(md: MarketData, params: ScreenParams | None = None,
            now: dt.datetime | None = None) -> ScreenResult:
    p = params or ScreenParams()
    now = now or md.asof
    notes = list(md.notes)
    S = float(md.spot)

    # --- contexte -----------------------------------------------------------
    if p.rate_override is not None:
        r = p.rate_override
    elif md.rate is not None and np.isfinite(md.rate):
        r = float(md.rate)
    else:
        r = DEFAULT_RATE
        notes.append(f"Taux sans risque inconnu : {DEFAULT_RATE:.1%} utilisé par défaut.")
    if p.div_override is not None:
        q_div = p.div_override
    elif md.div_yield is not None and np.isfinite(md.div_yield):
        q_div = float(md.div_yield)
    else:
        q_div = 0.0
    history = md.history
    fair = FairVolModel(history, p.fair_vol_model, p.manual_fair_vol)
    notes.extend(fair.notes)

    # --- préparation de la chaîne ------------------------------------------
    df = md.chain.copy()
    if p.style_override:
        df["style"] = p.style_override
    df["T"] = [year_fraction(e, now, md.expiry_tz, md.expiry_hour) for e in df["expiry"]]
    df["dte"] = [(e - now.date()).days for e in df["expiry"]]
    df = df[(df["dte"] >= p.dte_min) & (df["dte"] <= p.dte_max) & (df["T"] > 0)].copy()
    if df.empty:
        raise ValueError("Aucune option dans la plage de jours demandée.")
    df.loc[df["bid"] < 0, "bid"] = np.nan
    df.loc[df["ask"] <= 0, "ask"] = np.nan
    bad = df["ask"] < df["bid"]
    df.loc[bad, ["bid", "ask"]] = np.nan
    df["mid"] = np.where(df["bid"] > 0, (df["bid"] + df["ask"]) / 2, np.nan)
    df["is_call"] = df["type"] == "C"
    df["american"] = df["style"] == "american"

    # --- forward / q par échéance -------------------------------------------
    term_rows = []
    df["q"] = q_div
    for exp, g in df.groupby("expiry"):
        T = float(g["T"].iloc[0])
        calls = g[g["is_call"]].set_index("strike")["mid"]
        puts = g[~g["is_call"]].set_index("strike")["mid"]
        common = calls.index.intersection(puts.index)
        calls = calls[~calls.index.duplicated()]
        puts = puts[~puts.index.duplicated()]
        F_par = implied_forward(common, calls.reindex(common), puts.reindex(common), T, r)
        q_impl = implied_carry(F_par, S, T, r)
        european = not g["american"].any()
        use_impl = european and np.isfinite(q_impl) and -0.10 <= q_impl <= 0.25 and T > 3 / 365
        q_used = q_impl if use_impl else q_div
        df.loc[g.index, "q"] = q_used
        term_rows.append({"expiry": exp, "T": T, "dte": int(g["dte"].iloc[0]),
                          "forward": S * np.exp((r - q_used) * T), "q_utilise": q_used,
                          "q_parite": q_impl})

    # --- volatilités implicites ---------------------------------------------
    args = dict(S=S, K=df["strike"].values, T=df["T"].values, r=r, q=df["q"].values,
                is_call=df["is_call"].values, american=df["american"].values,
                steps=p.binomial_steps)
    df["iv_bid"] = implied_vol(np.where(df["bid"] > 0, df["bid"], np.nan), **args)
    df["iv_ask"] = implied_vol(df["ask"].values, **args)
    df["iv_mid"] = implied_vol(df["mid"].values, **args)

    term = pd.DataFrame(term_rows).set_index("expiry")
    for exp, g in df.groupby("expiry"):
        F = term.at[exp, "forward"]
        term.loc[exp, "atm_iv"] = _atm_iv(g, F)
        # straddle au strike le plus proche du forward ayant call et put
        both = g.pivot_table(index="strike", columns="type", values="mid", aggfunc="first")
        both = both.dropna() if {"C", "P"} <= set(both.columns) else pd.DataFrame()
        if not both.empty:
            k = both.index[np.argmin(np.abs(both.index.values - F))]
            term.loc[exp, "mouvement_implicite"] = (both.at[k, "C"] + both.at[k, "P"]) / S
        else:
            term.loc[exp, "mouvement_implicite"] = np.nan
    earn = md.earnings_date
    term["resultats_avant"] = [bool(earn and now.date() <= earn <= e) for e in term.index]
    base_fair = np.array([fair(T) for T in term["T"]])
    event_var = np.where(term["resultats_avant"], p.earnings_move**2 / term["T"].values, 0.0)
    term["vol_juste"] = np.sqrt(base_fair**2 + event_var)
    term["ecart_pts"] = (term["atm_iv"] - term["vol_juste"]) * 100
    days_tr = term["T"] * 252
    if history is not None and len(history) > 30:
        term["rv_meme_horizon"] = [realized_vol(history, max(10, int(round(d)))) for d in days_tr]
        term["mouvement_historique"] = [historical_abs_move(history, d) for d in days_tr]
    else:
        term["rv_meme_horizon"] = np.nan
        term["mouvement_historique"] = np.nan
    term = term.reset_index()

    # --- métriques par option ----------------------------------------------
    df = df.merge(term[["expiry", "atm_iv", "vol_juste", "rv_meme_horizon"]], on="expiry", how="left")
    sigma_g = np.where(np.isfinite(df["iv_mid"]), df["iv_mid"], df["iv_ask"])
    greeks = bs_greeks(S, df["strike"].values, df["T"].values, r, df["q"].values,
                       np.nan_to_num(sigma_g, nan=0.3), df["is_call"].values)
    for k, v in greeks.items():
        df[k] = np.where(np.isfinite(sigma_g), v, np.nan)
    df["prix_modele"] = option_price(S, df["strike"].values, df["T"].values, r, df["q"].values,
                                     df["vol_juste"].values, df["is_call"].values,
                                     df["american"].values, steps=p.binomial_steps)
    df["ecart_pts"] = (df["iv_ask"] - df["vol_juste"]) * 100
    df["ecart_mid_pts"] = (df["iv_mid"] - df["vol_juste"]) * 100
    df["spread_pts"] = (df["iv_ask"] - df["iv_bid"]) / 2 * 100
    df["edge_usd"] = (df["ask"] - df["prix_modele"]) * df["multiplier"]
    df["edge_pct"] = (df["ask"] - df["prix_modele"]) / df["ask"]
    df["iv_rv"] = df["iv_mid"] / df["rv_meme_horizon"]
    df["skew_pts"] = (df["iv_mid"] - df["atm_iv"]) * 100
    df["point_mort"] = np.where(df["is_call"], df["strike"] + df["ask"], df["strike"] - df["ask"])
    df["point_mort_pct"] = df["point_mort"] / S - 1
    df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"]
    df["cone_pct"] = _cone_pct(history, df)
    df["verdict"] = _verdict(df["ecart_pts"].values, p.rich_threshold, p.cheap_threshold)
    df["alertes"] = _flags(df, earn, now.date(), p)

    full = df.copy()

    # --- filtres -----------------------------------------------------------
    m = np.isfinite(df["iv_ask"]) & (df["bid"] > 0)
    if p.option_type in ("C", "P"):
        m &= df["type"] == p.option_type
    ad = df["delta"].abs()
    m &= (ad >= p.delta_min) & (ad <= p.delta_max)
    m &= df["spread_pct"] <= p.max_spread_pct
    if p.min_open_interest > 0:
        m &= df["open_interest"].fillna(0) >= p.min_open_interest
    if p.min_volume > 0:
        m &= df["volume"].fillna(0) >= p.min_volume
    options = df[m].sort_values("ecart_pts").reset_index(drop=True)

    summary = _summary(md, term, fair, history, r, q_div, now)
    if p.earnings_move > 0 and term["resultats_avant"].any():
        notes.append(f"Vol juste majorée d'un mouvement de {p.earnings_move:.1%} aux résultats "
                     "pour les échéances qui les incluent.")
    cone = vol_cone(history) if history is not None and len(history) > 130 else pd.DataFrame()
    return ScreenResult(options=options, full=full, term=term, summary=summary, cone=cone,
                        fair_model=fair, notes=notes)


def _cone_pct(history, df) -> np.ndarray:
    out = np.full(len(df), np.nan)
    if history is None or len(history) < 60:
        return out
    for dte, idx in df.groupby("dte").groups.items():
        w = int(max(5, min(round(dte * 252 / 365), len(history) // 3)))
        dist = close_to_close(history.iloc[-(504 + w + 1):], w).dropna().values[-504:]
        if len(dist) < 20:
            continue
        iv = df.loc[idx, "iv_mid"].values
        pos = df.index.get_indexer(idx)
        out[pos] = np.where(np.isfinite(iv), (dist[None, :] < iv[:, None]).mean(axis=1) * 100, np.nan)
    return out


def _flags(df: pd.DataFrame, earn, today, p: ScreenParams) -> list[str]:
    out = []
    for row in df.itertuples(index=False):
        f = []
        if earn and today <= earn <= row.expiry:
            f.append("Résultats avant échéance")
        if np.isfinite(row.spread_pts) and row.spread_pts > p.wide_spread_pts:
            f.append(f"Spread large ({row.spread_pts:.1f} pts)")
        if (row.verdict == VERDICT_RICH and np.isfinite(row.ecart_mid_pts)
                and row.ecart_mid_pts <= p.rich_threshold):
            f.append("Cher surtout à cause du spread : vise le mid")
        if (np.isfinite(row.vendor_iv) and np.isfinite(row.iv_mid)
                and abs(row.iv_mid - row.vendor_iv) * 100 > p.vendor_iv_tolerance):
            f.append(f"IV fournisseur {row.vendor_iv:.1%}")
        out.append(" · ".join(f))
    return out


def _implied_event_move(term: pd.DataFrame) -> float:
    """Mouvement implicite des résultats, tiré des IV ATM des échéances qui
    encadrent la publication : e = T2 (σ2² − σ1²), en supposant que la vol
    hors événement est celle de l'échéance d'avant (σ1)."""
    before = term[~term["resultats_avant"] & np.isfinite(term["atm_iv"])]
    after = term[term["resultats_avant"] & np.isfinite(term["atm_iv"])]
    if before.empty or after.empty:
        return float("nan")
    s1 = before.iloc[-1]["atm_iv"]
    a = after.iloc[0]
    var = a["T"] * (a["atm_iv"] ** 2 - s1**2)
    return float(np.sqrt(var)) if var > 0 else float("nan")


def _summary(md, term, fair, history, r, q, now) -> dict:
    atm30 = _interp_term(term, "atm_iv", 30)
    fair30 = fair(30 / 365)
    s = {
        "ticker": md.ticker, "source": md.source, "spot": md.spot, "devise": md.currency,
        "taux": r, "dividende": q, "asof": now,
        "atm_iv_30j": atm30, "vol_juste_30j": fair30,
        "ecart_30j_pts": (atm30 - fair30) * 100 if np.isfinite(atm30) else np.nan,
        "modele_vol": fair.label(),
        "rv_20j": np.nan, "rv_60j": np.nan, "cone_pct_30j": np.nan,
        "iv_rank": np.nan, "iv_percentile": np.nan,
        "resultats": md.earnings_date,
        "jours_avant_resultats": (md.earnings_date - now.date()).days if md.earnings_date else None,
    }
    s["mouvement_resultats_implicite"] = _implied_event_move(term)
    if history is not None and len(history) > 70:
        s["rv_20j"] = realized_vol(history, 20)
        s["rv_60j"] = realized_vol(history, 60)
        s["cone_pct_30j"] = cone_percentile(history, atm30, 21)
    if md.iv_history is not None and len(md.iv_history) > 20:
        current = float(pd.Series(md.iv_history).dropna().iloc[-1])
        s["iv_rank"], s["iv_percentile"] = iv_rank(md.iv_history, current)
    if fair.garch is not None:
        g = fair.garch
        s.update({"garch_persistance": g.persistence, "garch_vol_long_terme": g.long_run_vol,
                  "garch_demi_vie_j": g.half_life_days})
    return s
