"""Ligne de commande.

Exemples :
    python -m option_screener SPY
    python -m option_screener AAPL --source ibkr --type call --dte 20-60
    python -m option_screener "SPX Index" --source bloomberg --type put --delta 0.15-0.40
    python -m option_screener XYZ --source csv --csv chaine.csv --spot 52.3 --vol-manuelle 0.35
    python -m option_screener DEMO --source demo --export resultat.csv
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from .config import load_config
from .screener import ScreenParams, analyze
from .sources import make_provider


def _range(text: str, cast=float):
    a, b = text.replace(" ", "").split("-")
    return cast(a), cast(b)


def build_parser(cfg: dict) -> argparse.ArgumentParser:
    sc = cfg.get("screener", {})
    g = cfg.get("general", {})
    p = argparse.ArgumentParser(prog="python -m option_screener",
                                description="Screener : la vol implicite est-elle trop chère ?")
    p.add_argument("ticker", nargs="?", default=g.get("ticker", "SPY"))
    p.add_argument("--source", default=g.get("source", "yahoo"),
                   choices=["demo", "yahoo", "ibkr", "bloomberg", "csv"])
    p.add_argument("--csv", help="fichier CSV de la chaîne (source csv)")
    p.add_argument("--historique", help="CSV d'historique OHLC (source csv, facultatif)")
    p.add_argument("--spot", type=float, help="prix du sous-jacent (source csv)")
    p.add_argument("--style", choices=["american", "european"], help="force le style d'exercice")
    p.add_argument("--type", default={"C": "call", "P": "put"}.get(sc.get("type"), "both"),
                   choices=["call", "put", "both"])
    p.add_argument("--dte", default=f"{sc.get('dte_min', 14)}-{sc.get('dte_max', 90)}",
                   help="plage de jours avant échéance, ex. 20-60")
    p.add_argument("--delta", default=f"{sc.get('delta_min', 0.2)}-{sc.get('delta_max', 0.7)}",
                   help="plage de |delta|, ex. 0.25-0.60")
    p.add_argument("--modele", default=sc.get("modele_vol", "garch"),
                   choices=["garch", "ewma", "yang-zhang"], help="modèle de vol juste")
    p.add_argument("--vol-manuelle", type=float, help="vol juste fixée à la main (ex. 0.25)")
    p.add_argument("--cher", type=float, default=sc.get("seuil_cher", 3.0))
    p.add_argument("--bon-marche", type=float, default=sc.get("seuil_bon_marche", -1.0))
    p.add_argument("--spread-max", type=float, default=sc.get("spread_max_pct", 0.30))
    p.add_argument("--oi-min", type=float, default=sc.get("oi_min", 0))
    p.add_argument("--move-resultats", type=float, default=0.0,
                   help="mouvement attendu aux résultats (ex. 0.06) ajouté à la vol juste")
    p.add_argument("--taux", type=float, help="taux sans risque forcé (ex. 0.043)")
    p.add_argument("--differe", action="store_true", help="IBKR : données différées")
    p.add_argument("--dernier-prix", action="store_true",
                   help="Yahoo : utiliser le dernier prix quand bid/ask sont vides (hors séance)")
    p.add_argument("--top", type=int, default=25, help="nombre de lignes affichées")
    p.add_argument("--export", help="enregistre le tableau complet en CSV")
    return p


def main(argv=None) -> int:
    cfg = load_config()
    args = build_parser(cfg).parse_args(argv)
    dte_min, dte_max = _range(args.dte, int)
    d_min, d_max = _range(args.delta)
    provider = make_provider(args.source, cfg, csv_file=args.csv, csv_spot=args.spot,
                             csv_history=args.historique, csv_style=args.style or "american",
                             yahoo_use_last=args.dernier_prix,
                             ibkr_delayed=True if args.differe else None)
    md = provider.load(args.ticker, dte_min=dte_min, dte_max=dte_max)
    params = ScreenParams(
        option_type={"call": "C", "put": "P"}.get(args.type, "both"),
        dte_min=dte_min, dte_max=dte_max, delta_min=d_min, delta_max=d_max,
        fair_vol_model="manual" if args.vol_manuelle else args.modele,
        manual_fair_vol=args.vol_manuelle, rich_threshold=args.cher,
        cheap_threshold=args.bon_marche, max_spread_pct=args.spread_max,
        min_open_interest=args.oi_min, rate_override=args.taux, style_override=args.style,
        earnings_move=args.move_resultats,
    )
    res = analyze(md, params)
    print(format_report(res, args.top))
    if args.export:
        res.options.to_csv(args.export, index=False)
        print(f"\nTableau complet enregistré dans {args.export}")
    return 0


def _pct(x, digits=1):
    return "n/d" if x is None or pd.isna(x) else f"{x * 100:.{digits}f} %"


def _num(x):
    return "n/d" if x is None or pd.isna(x) else f"{x:.0f}"


def format_report(res, top: int = 25) -> str:
    s = res.summary
    lines = [
        f"{s['ticker']} ({s['source']}) spot {s['spot']:.2f} {s['devise']}  ·  "
        f"taux {_pct(s['taux'], 2)}  ·  dividende {_pct(s['dividende'], 2)}",
        f"IV ATM 30 j {_pct(s['atm_iv_30j'])}  ·  vol juste 30 j ({s['modele_vol']}) "
        f"{_pct(s['vol_juste_30j'])}  ·  écart {s['ecart_30j_pts']:+.1f} pts",
        f"RV 20 j {_pct(s['rv_20j'])}  ·  RV 60 j {_pct(s['rv_60j'])}  ·  "
        f"IV rank {_num(s['iv_rank'])}  ·  percentile cône 30 j {_num(s['cone_pct_30j'])}",
    ]
    if s.get("resultats"):
        lines.append(f"Prochains résultats : {s['resultats']} (dans {s['jours_avant_resultats']} j)"
                     f"  ·  mouvement implicite des résultats ≈ {_pct(s['mouvement_resultats_implicite'])}")
    for n in res.notes:
        lines.append(f"Note : {n}")
    lines.append("")
    t = res.term
    lines.append("Structure par terme")
    lines.append(pd.DataFrame({
        "échéance": t["expiry"], "jours": t["dte"],
        "IV ATM": t["atm_iv"].map(_pct), "vol juste": t["vol_juste"].map(_pct),
        "écart pts": t["ecart_pts"].map(lambda x: f"{x:+.1f}"),
        "mvt implicite": t["mouvement_implicite"].map(_pct),
        "mvt historique": t["mouvement_historique"].map(_pct),
        "résultats": t["resultats_avant"].map({True: "oui", False: ""}),
    }).to_string(index=False))
    lines.append("")
    o = res.options.head(top)
    if o.empty:
        lines.append("Aucune option ne passe les filtres.")
        return "\n".join(lines)
    lines.append(f"Options (les moins chères d'abord, {len(res.options)} au total)")
    lines.append(pd.DataFrame({
        "échéance": o["expiry"], "j": o["dte"], "type": o["type"], "strike": o["strike"],
        "bid": o["bid"], "ask": o["ask"], "IV ask": o["iv_ask"].map(_pct),
        "vol juste": o["vol_juste"].map(_pct), "écart pts": o["ecart_pts"].map(lambda x: f"{x:+.1f}"),
        "edge $": o["edge_usd"].map(lambda x: f"{x:+.0f}"), "delta": o["delta"].round(2),
        "verdict": o["verdict"], "alertes": o["alertes"],
    }).to_string(index=False))
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
