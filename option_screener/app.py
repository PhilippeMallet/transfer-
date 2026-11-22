"""Appli Streamlit : screener « la vol implicite est-elle trop chère ? ».

Lancement (depuis la racine du repo) :
    streamlit run option_screener/app.py
ou double-clic sur option_screener/lancer.bat (Windows) / lancer.sh.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from option_screener import charts  # noqa: E402
from option_screener.config import CONFIG_PATH, load_config  # noqa: E402
from option_screener.screener import (VERDICT_CHEAP, VERDICT_RICH, ScreenParams,  # noqa: E402
                                      analyze)
from option_screener.sources import SOURCES, detect_sources, make_provider  # noqa: E402

st.set_page_config(page_title="Screener de vol", page_icon="📈", layout="wide")

cfg = load_config()
gen, sc_cfg = cfg.get("general", {}), cfg.get("screener", {})


def theme_mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:  # noqa: BLE001
        return "light"


def pct(x, d=1):
    return "n/d" if x is None or pd.isna(x) else f"{x * 100:.{d}f} %"


# ---------------------------------------------------------------------------
# Barre latérale
# ---------------------------------------------------------------------------
if "sources" not in st.session_state:
    st.session_state.sources = detect_sources(cfg)

with st.sidebar:
    st.header("Données")
    keys = list(SOURCES)
    default_src = gen.get("source", "yahoo")
    avail = st.session_state.sources
    if not avail.get(default_src, (False,))[0]:
        default_src = "yahoo" if avail["yahoo"][0] else "demo"
    source = st.selectbox(
        "Source", keys, index=keys.index(default_src),
        format_func=lambda k: SOURCES[k] + ("" if avail[k][0] else " · indisponible"))
    ok, why = avail[source]
    if not ok:
        st.warning(f"{SOURCES[source]} : {why}")
    if st.button("Re-détecter les sources", width="stretch"):
        st.session_state.sources = detect_sources(cfg)
        st.rerun()

    placeholder = {"bloomberg": "AAPL US Equity", "ibkr": "AAPL", "demo": "DEMO"}.get(source, "SPY")
    ticker = st.text_input("Sous-jacent", value="DEMO" if source == "demo" else gen.get("ticker", "SPY"),
                           placeholder=placeholder,
                           help="Yahoo : SPY, AAPL, ^SPX · IBKR : AAPL, SPX, ESTX50 ou SYMBOLE:BOURSE:DEVISE · "
                                "Bloomberg : AAPL US Equity, SPX Index")

    csv_kwargs = {}
    yahoo_last = False
    ib_delayed = None
    if source == "csv":
        chain_file = st.file_uploader("Chaîne d'options (CSV)", type=["csv", "txt"])
        spot_in = st.number_input("Spot du sous-jacent (0 = lu dans le CSV)", min_value=0.0, value=0.0, step=0.01)
        hist_file = st.file_uploader("Historique OHLC (CSV, facultatif)", type=["csv", "txt"],
                                     help="Sinon l'historique est pris sur Yahoo avec le ticker ci-dessus.")
        csv_style = st.radio("Style d'exercice", ["american", "european"], horizontal=True,
                             format_func={"american": "Américain", "european": "Européen"}.get)
        dayfirst = st.checkbox("Dates au format jour/mois", value=True)
        csv_kwargs = dict(csv_file=chain_file.getvalue() if chain_file else None,
                          csv_spot=spot_in or None,
                          csv_history=hist_file.getvalue() if hist_file else None,
                          csv_style=csv_style, csv_dayfirst=dayfirst)
    elif source == "yahoo":
        yahoo_last = st.checkbox("Hors séance : utiliser le dernier prix",
                                 help="Yahoo renvoie bid/ask à 0 quand le marché est fermé.")
    elif source == "ibkr":
        ib_delayed = st.checkbox("Données différées (sans abonnement OPRA)",
                                 value=bool(cfg.get("ibkr", {}).get("donnees_differees", False)))

    st.header("Filtres")
    opt_type = st.radio("Sens", ["C", "P", "both"], horizontal=True,
                        index=["C", "P", "both"].index(sc_cfg.get("type", "both")),
                        format_func={"C": "Calls (haussier)", "P": "Puts (baissier)", "both": "Les deux"}.get)
    dte = st.slider("Jours avant échéance", 0, 365,
                    (int(sc_cfg.get("dte_min", 14)), int(sc_cfg.get("dte_max", 90))))
    delta = st.slider("|Delta|", 0.05, 0.95,
                      (float(sc_cfg.get("delta_min", 0.2)), float(sc_cfg.get("delta_max", 0.7))), 0.05)
    spread_max = st.slider("Spread max (% du mid)", 1, 100, int(float(sc_cfg.get("spread_max_pct", 0.3)) * 100))
    c1, c2 = st.columns(2)
    oi_min = c1.number_input("OI min", 0, value=int(sc_cfg.get("oi_min", 0)), step=50)
    vol_min = c2.number_input("Volume min", 0, value=int(sc_cfg.get("volume_min", 0)), step=10)

    st.header("Vol juste")
    models = ["garch", "ewma", "yang-zhang", "manual"]
    model = st.selectbox("Modèle", models, index=models.index(sc_cfg.get("modele_vol", "garch")),
                         format_func={"garch": "GARCH(1,1) — prévision jusqu'à l'échéance",
                                      "ewma": "EWMA 0,94 — vol récente",
                                      "yang-zhang": "Vol réalisée Yang-Zhang (fenêtre = durée de vie)",
                                      "manual": "Manuelle"}.get)
    manual_vol = None
    if model == "manual":
        manual_vol = st.number_input("Vol juste (%)", 1.0, 300.0, 25.0, 0.5) / 100
    earn_move = st.number_input("Mouvement attendu aux résultats (%)", 0.0, 50.0, 0.0, 0.5,
                                help="Ajouté à la vol juste des échéances qui incluent la publication. "
                                     "0 = pas d'ajout (l'option est alors signalée).") / 100

    st.header("Verdict")
    c1, c2 = st.columns(2)
    rich = c1.number_input("Cher si > (pts)", -10.0, 30.0, float(sc_cfg.get("seuil_cher", 3.0)), 0.5)
    cheap = c2.number_input("Bon marché si < (pts)", -30.0, 10.0, float(sc_cfg.get("seuil_bon_marche", -1.0)), 0.5)

    with st.expander("Avancé"):
        rate_on = st.checkbox("Forcer le taux sans risque")
        rate = st.number_input("Taux (%)", -2.0, 20.0, 4.0, 0.05) / 100 if rate_on else None
        style_ovr = st.selectbox("Style d'exercice", [None, "american", "european"],
                                 format_func=lambda s: {None: "Celui de la source", "american": "Américain",
                                                        "european": "Européen"}[s])
        st.caption(f"Config locale : {'trouvée' if CONFIG_PATH.exists() else 'absente (valeurs par défaut)'}")

    load = st.button("Charger les données", type="primary", width="stretch", disabled=not ok)

# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------
if load:
    try:
        with st.spinner(f"Chargement {ticker} depuis {SOURCES[source]}…"):
            provider = make_provider(source, cfg, yahoo_use_last=yahoo_last, ibkr_delayed=ib_delayed,
                                     **csv_kwargs)
            md = provider.load(ticker.strip(), dte_min=dte[0], dte_max=dte[1])
        st.session_state.md = md
        st.session_state.loaded_range = dte
        st.session_state.results = {}
    except Exception as exc:  # noqa: BLE001
        st.error(f"Échec du chargement : {exc}")

st.title("Screener de vol implicite")
if "md" not in st.session_state:
    st.markdown(
        "Choisis une source et un sous-jacent dans la barre latérale, puis **Charger les données**.\n\n"
        "Pour chaque option, l'outil compare la **vol implicite à l'ask** (ce que tu paies) à une "
        "**vol juste** estimée sur l'historique, et donne un verdict : *Bon marché*, *Neutre* ou *Cher*. "
        "Tu choisis le sens (calls / puts), il s'occupe du prix.")
    unavailable = [f"{SOURCES[k]} ({v[1]})" for k, v in avail.items() if not v[0]]
    if unavailable:
        st.caption("Sources indisponibles sur ce PC : " + " · ".join(unavailable))
    st.stop()

md = st.session_state.md
lo, hi = st.session_state.loaded_range
if dte[0] < lo or dte[1] > hi:
    st.info(f"Les données chargées couvrent {lo}–{hi} jours : clique « Charger les données » pour élargir.")

params = ScreenParams(
    option_type=opt_type, dte_min=dte[0], dte_max=dte[1], delta_min=delta[0], delta_max=delta[1],
    min_open_interest=oi_min, min_volume=vol_min, max_spread_pct=spread_max / 100,
    fair_vol_model=model, manual_fair_vol=manual_vol, rich_threshold=rich, cheap_threshold=cheap,
    rate_override=rate, style_override=style_ovr, earnings_move=earn_move,
)
key = repr(params)
cache = st.session_state.setdefault("results", {})
if key not in cache:
    try:
        with st.spinner("Calcul des vols implicites…"):
            cache[key] = analyze(md, params)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Calcul impossible : {exc}")
        st.stop()
res = cache[key]
s = res.summary
mode = theme_mode()

# ---------------------------------------------------------------------------
# En-tête
# ---------------------------------------------------------------------------
st.caption(f"{s['ticker']} · source {s['source']} · {s['asof']:%d/%m/%Y %H:%M} UTC · "
           f"taux {pct(s['taux'], 2)} · dividende {pct(s['dividende'], 2)}")
k = st.columns(6)
k[0].metric(f"Spot ({s['devise']})", f"{s['spot']:,.2f}".replace(",", " "))
k[1].metric("IV ATM 30 j", pct(s["atm_iv_30j"]))
k[2].metric(f"Vol juste 30 j ({s['modele_vol']})", pct(s["vol_juste_30j"]),
            delta=None if pd.isna(s["ecart_30j_pts"]) else f"IV {s['ecart_30j_pts']:+.1f} pts",
            delta_color="inverse")
k[3].metric("Vol réalisée 20 j", pct(s["rv_20j"]), help=f"Vol réalisée 60 j : {pct(s['rv_60j'])}")
if not pd.isna(s["iv_rank"]):
    k[4].metric("IV rank 1 an", f"{s['iv_rank']:.0f}", help=f"IV percentile : {s['iv_percentile']:.0f}")
else:
    k[4].metric("IV dans le cône 30 j", "n/d" if pd.isna(s["cone_pct_30j"]) else f"{s['cone_pct_30j']:.0f}e pct",
                help="Percentile de l'IV ATM 30 j dans l'historique de la vol réalisée 21 séances.")
if s.get("resultats"):
    k[5].metric(f"Résultats (J-{s['jours_avant_resultats']})", f"{s['resultats']:%d/%m}",
                help="Mouvement implicite des résultats ≈ " + pct(s.get("mouvement_resultats_implicite")))
else:
    k[5].metric("Résultats", "aucun connu")

gap = s["ecart_30j_pts"]
if not pd.isna(gap):
    if gap > rich:
        st.warning(f"À 30 jours, l'IV ATM est **{gap:+.1f} pts** au-dessus de la vol juste : la vol est chère. "
                   "Acheter l'option seule paie cette prime ; un spread (achat + vente d'un strike plus loin) "
                   "en neutralise une partie.")
    elif gap < cheap:
        st.success(f"À 30 jours, l'IV ATM est **{gap:+.1f} pts** sous la vol juste : la vol est bon marché.")
    else:
        st.info(f"À 30 jours, l'IV ATM est à **{gap:+.1f} pts** de la vol juste : pas d'excès notable.")
if s.get("resultats") and not pd.isna(s.get("mouvement_resultats_implicite", np.nan)):
    st.caption(f"Le marché price un mouvement d'environ **{pct(s['mouvement_resultats_implicite'])}** "
               f"aux résultats du {s['resultats']:%d/%m}. Renseigne ton estimation dans « Mouvement attendu "
               "aux résultats » pour l'intégrer à la vol juste.")
for n in res.notes:
    st.caption(f"ⓘ {n}")

tab_opt, tab_term, tab_smile, tab_cone, tab_help = st.tabs(
    ["Options", "Structure par terme", "Smile", "Cône de vol", "Méthode"])

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
with tab_opt:
    o = res.options
    counts = o["verdict"].value_counts()
    st.markdown(f"**{len(o)} options** retenues · {counts.get(VERDICT_CHEAP, 0)} bon marché · "
                f"{counts.get('Neutre', 0)} neutres · {counts.get(VERDICT_RICH, 0)} chères "
                "· triées de la moins chère à la plus chère (IV ask − vol juste)")
    if o.empty:
        st.info("Aucune option ne passe les filtres : élargis la plage de delta, de jours ou le spread max.")
    else:
        view = pd.DataFrame({
            "Échéance": pd.to_datetime(o["expiry"]).dt.strftime("%d/%m/%y"), "Jours": o["dte"],
            "Type": o["type"], "Strike": o["strike"], "Bid": o["bid"], "Ask": o["ask"],
            "IV bid": o["iv_bid"] * 100, "IV ask": o["iv_ask"] * 100, "Vol juste": o["vol_juste"] * 100,
            # arrondi + 0.0 : évite l'affichage « -0 »
            "Écart (pts)": o["ecart_pts"].round(1) + 0.0, "Edge $": o["edge_usd"].round(0) + 0.0,
            "Edge %": (o["edge_pct"] * 100).round(0) + 0.0,
            "Verdict": o["verdict"], "Delta": o["delta"], "Spread (pts)": o["spread_pts"],
            "Skew (pts)": o["skew_pts"].round(1) + 0.0, "IV/RV": o["iv_rv"], "Point mort": o["point_mort"],
            "OI": o["open_interest"], "Volume": o["volume"], "Alertes": o["alertes"],
        })
        vc = charts.VERDICT_COLORS[mode]

        def color_verdict(v):
            fg, bg = vc.get(v, ("inherit", "transparent"))
            return f"color: {fg}; background-color: {bg}; font-weight: 600"

        fmt = {"Strike": "{:.2f}", "Bid": "{:.2f}", "Ask": "{:.2f}", "IV bid": "{:.1f}", "IV ask": "{:.1f}",
               "Vol juste": "{:.1f}", "Écart (pts)": "{:+.1f}", "Edge $": "{:+.0f}", "Edge %": "{:+.0f}",
               "Delta": "{:.2f}", "Spread (pts)": "{:.1f}", "Skew (pts)": "{:+.1f}", "IV/RV": "{:.2f}",
               "Point mort": "{:.2f}", "OI": "{:.0f}", "Volume": "{:.0f}"}
        styled = view.style.format(fmt, na_rep="–").map(color_verdict, subset=["Verdict"])
        event = st.dataframe(styled, width="stretch", hide_index=True, height=440,
                             on_select="rerun", selection_mode="single-row", key="table")
        st.download_button("Exporter en CSV", o.to_csv(index=False).encode("utf-8"),
                           file_name=f"screener_{s['ticker']}.csv", mime="text/csv")

        rows = event.selection.rows if event and event.selection else []
        st.subheader("Détail")
        if not rows:
            st.caption("Clique une ligne du tableau pour voir le détail de l'option.")
        else:
            r = o.iloc[rows[0]]
            label = f"{s['ticker']} {r['expiry']:%d/%m/%Y} {'Call' if r['type'] == 'C' else 'Put'} {r['strike']:g}"
            st.markdown(f"**{label}** · verdict **{r['verdict']}**" + (f" · {r['alertes']}" if r["alertes"] else ""))
            d = st.columns(4)
            d[0].metric("Ask payé", f"{r['ask']:.2f}", help=f"bid {r['bid']:.2f}, mid {r['mid']:.2f}")
            d[1].metric("Prix à vol juste", f"{r['prix_modele']:.2f}",
                        delta=f"{-r['edge_usd']:+.0f} $ / contrat", delta_color="normal",
                        help="Écart positif : l'option coûte moins que sa valeur à vol juste.")
            d[2].metric("IV ask / vol juste", f"{pct(r['iv_ask'])} / {pct(r['vol_juste'])}",
                        delta=f"{r['ecart_pts']:+.1f} pts", delta_color="inverse")
            d[3].metric("Point mort à l'échéance", f"{r['point_mort']:.2f}",
                        delta=f"{r['point_mort_pct'] * 100:+.1f} % vs spot", delta_color="off")
            d = st.columns(4)
            d[0].metric("Delta", f"{r['delta']:.2f}")
            d[1].metric("Gamma", f"{r['gamma']:.4f}")
            d[2].metric("Vega (1 pt de vol)", f"{r['vega'] * r['multiplier']:.0f} $",
                        help="Gain ou perte par contrat si la vol implicite bouge d'un point.")
            d[3].metric("Theta (par jour)", f"{r['theta'] * r['multiplier']:.0f} $")
            trow = res.term[res.term["expiry"] == r["expiry"]].iloc[0]
            cone_txt = "n/d" if pd.isna(r["cone_pct"]) else f"{r['cone_pct']:.0f}e percentile"
            st.caption(
                f"Mouvement implicite à cette échéance (straddle ATM) : {pct(trow['mouvement_implicite'])} · "
                f"mouvement historique moyen sur la même durée : {pct(trow['mouvement_historique'])} · "
                f"IV mid {pct(r['iv_mid'])} · position dans le cône de vol réalisée : {cone_txt}")

# ---------------------------------------------------------------------------
# Structure par terme
# ---------------------------------------------------------------------------
with tab_term:
    st.plotly_chart(charts.term_structure(res.term, s.get("resultats"), mode), width="stretch")
    t = res.term
    st.dataframe(pd.DataFrame({
        "Échéance": pd.to_datetime(t["expiry"]).dt.strftime("%d/%m/%y"), "Jours": t["dte"],
        "IV ATM (%)": t["atm_iv"] * 100, "Vol juste (%)": t["vol_juste"] * 100, "Écart (pts)": t["ecart_pts"],
        "RV même horizon (%)": t["rv_meme_horizon"] * 100,
        "Mvt implicite (%)": t["mouvement_implicite"] * 100,
        "Mvt historique (%)": t["mouvement_historique"] * 100,
        "Forward": t["forward"], "q utilisé (%)": t["q_utilise"] * 100, "q parité (%)": t["q_parite"] * 100,
        "Résultats inclus": t["resultats_avant"].map({True: "oui", False: ""}),
    }).style.format({"IV ATM (%)": "{:.1f}", "Vol juste (%)": "{:.1f}", "Écart (pts)": "{:+.1f}",
                     "RV même horizon (%)": "{:.1f}", "Mvt implicite (%)": "{:.1f}",
                     "Mvt historique (%)": "{:.1f}", "Forward": "{:.2f}", "q utilisé (%)": "{:.2f}",
                     "q parité (%)": "{:.2f}"}, na_rep="–"),
        width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Smile
# ---------------------------------------------------------------------------
with tab_smile:
    exps = list(res.term["expiry"])
    default_i = int(np.argmin(np.abs(res.term["dte"].values - 30)))
    exp = st.selectbox("Échéance", exps, index=default_i, format_func=lambda e: f"{e:%d/%m/%Y}")
    fwd = float(res.term.loc[res.term["expiry"] == exp, "forward"].iloc[0])
    st.plotly_chart(charts.smile(res.full, exp, fwd, mode), width="stretch")
    st.caption("Côté OTM uniquement : puts sous le forward, calls au-dessus. La bande montre la fourchette "
               "bid/ask en vol : c'est le coût de l'aller-retour.")

# ---------------------------------------------------------------------------
# Cône
# ---------------------------------------------------------------------------
with tab_cone:
    st.plotly_chart(charts.cone(res.cone, res.term, mode), width="stretch")
    if not res.cone.empty:
        c = res.cone
        st.dataframe(pd.DataFrame({
            "Fenêtre (séances)": c["fenetre"], "Min": c["min"] * 100, "25 %": c["p25"] * 100,
            "Médiane": c["mediane"] * 100, "75 %": c["p75"] * 100, "Max": c["max"] * 100,
            "Actuelle": c["actuelle"] * 100,
        }).style.format("{:.1f}", subset=["Min", "25 %", "Médiane", "75 %", "Max", "Actuelle"]),
            width="stretch", hide_index=True)
    if res.fair_model.garch is not None:
        g = res.fair_model.garch
        st.caption(f"GARCH(1,1) : persistance {g.persistence:.3f} · vol long terme {pct(g.long_run_vol)} · "
                   f"demi-vie d'un choc {g.half_life_days:.0f} séances · {g.n_obs} rendements.")

# ---------------------------------------------------------------------------
# Méthode
# ---------------------------------------------------------------------------
with tab_help:
    st.markdown("""
**Question posée** : à ce prix, est-ce que je paie la vol trop cher ?

- **IV ask** : vol implicite du prix *ask*, celui que tu paies en achetant. Calculée par
  arbre binomial (exercice américain) ou Black-Scholes (européen), avec taux et dividendes.
- **Vol juste** : ce que le sous-jacent devrait réaliser d'ici l'échéance, selon le modèle choisi.
  Le GARCH(1,1) part de la vol récente et la fait revenir vers sa moyenne de long terme :
  il donne une vol différente par échéance.
- **Écart (pts)** = IV ask − vol juste. Au-dessus du seuil « Cher », tu paies une prime de vol
  importante. Les vendeurs d'options exigent en moyenne une petite prime (prime de risque de
  variance) : d'où un seuil « Cher » au-dessus de zéro.
- **Edge $** = (ask − prix à vol juste) × multiplicateur. Négatif = l'option est sous sa valeur.
- **Spread (pts)** : demi-fourchette bid/ask en points de vol. Si l'option n'est chère qu'à cause
  du spread, l'alerte te le dit : place un ordre limite au mid.
- **Résultats** : une publication avant l'échéance gonfle l'IV. Renseigne le mouvement que tu
  attends pour qu'il soit ajouté à la vol juste ; sinon l'option est simplement signalée.
- **IV rank** (IBKR, Bloomberg) : position de l'IV actuelle entre son min et son max sur 1 an.
  Sans historique d'IV, l'IV est placée dans le **cône de vol réalisée**.
- Les grecques sont celles de Black-Scholes à l'IV mid (approximation pour l'américain).
""")
