import datetime as dt

import numpy as np
import pandas as pd
import pytest

from option_screener.__main__ import main as cli_main
from option_screener.data.base import MarketData, normalize_chain
from option_screener.data.demo import make_demo_market, simulate_history
from option_screener.pricing import option_price
from option_screener.screener import (VERDICT_CHEAP, VERDICT_NEUTRAL, VERDICT_RICH, ScreenParams,
                                      analyze, year_fraction)

NOW = dt.datetime(2026, 3, 2, 15, 0, tzinfo=dt.timezone.utc)
S, R, Q = 100.0, 0.04, 0.0
FAIR = 0.25


def _chain(vol_by_days: dict, style="european", half_spread=0.002, now=NOW, event=None):
    """Chaîne synthétique : vol plate par échéance (ou fonction de T)."""
    rows = []
    for days, vol in vol_by_days.items():
        exp = now.date() + dt.timedelta(days=days)
        T = year_fraction(exp, now)
        sig = vol(T) if callable(vol) else vol
        strikes = np.arange(70, 131, 2.5)
        for is_call in (True, False):
            px = option_price(S, strikes, T, R, Q, sig, is_call, american=style == "american")
            for k, p in zip(strikes, px):
                if p < 0.05:
                    continue
                rows.append({"expiry": exp, "strike": k, "type": "C" if is_call else "P",
                             "bid": p * (1 - half_spread), "ask": p * (1 + half_spread),
                             "volume": 100, "open_interest": 1000, "style": style})
    return normalize_chain(pd.DataFrame(rows), style)


def _md(chain, earnings=None, history=True):
    hist = simulate_history(spot_end=S, end=NOW.date()) if history else None
    return MarketData(ticker="TEST", spot=S, chain=chain, history=hist, rate=R, div_yield=Q,
                      earnings_date=earnings, style="european", source="test", asof=NOW)


def manual(**kw):
    base = dict(dte_min=0, dte_max=400, delta_min=0.05, delta_max=0.95, max_spread_pct=1.0,
                fair_vol_model="manual", manual_fair_vol=FAIR)
    base.update(kw)
    return ScreenParams(**base)


def test_verdicts_follow_vol_gap():
    chain = _chain({30: FAIR + 0.08, 60: FAIR - 0.06, 90: FAIR + 0.01})
    res = analyze(_md(chain), manual())
    by_dte = res.options.groupby("dte")["verdict"].agg(lambda v: set(v))
    assert by_dte.loc[30] == {VERDICT_RICH}
    assert by_dte.loc[60] == {VERDICT_CHEAP}
    assert by_dte.loc[90] == {VERDICT_NEUTRAL}
    # l'IV mid retrouve la vol de cotation, l'IV ask est un peu au-dessus
    o = res.options
    target = o["dte"].map({30: FAIR + 0.08, 60: FAIR - 0.06, 90: FAIR + 0.01})
    np.testing.assert_allclose(o["iv_mid"], target, atol=2e-3)
    assert (o["iv_ask"] >= o["iv_mid"] - 1e-9).all()
    # tri : les moins chères d'abord
    assert o["ecart_pts"].is_monotonic_increasing


def test_edge_sign_and_model_price():
    res = analyze(_md(_chain({45: FAIR + 0.05})), manual())
    o = res.options
    assert (o["edge_usd"] > 0).all()
    expected = option_price(S, o["strike"].values, o["T"].values, R, Q, FAIR, o["is_call"].values)
    np.testing.assert_allclose(o["prix_modele"], expected, rtol=1e-9)


def test_american_chain():
    chain = _chain({40: FAIR + 0.06, 120: FAIR - 0.05}, style="american")
    res = analyze(_md(chain), manual())
    o = res.options[res.options["delta"].abs() < 0.8]
    assert set(o[o["dte"] == 40]["verdict"]) == {VERDICT_RICH}
    assert set(o[o["dte"] == 120]["verdict"]) == {VERDICT_CHEAP}


def test_filters():
    chain = _chain({30: FAIR, 60: FAIR})
    res = analyze(_md(chain), manual(option_type="P", delta_min=0.3, delta_max=0.6, dte_min=50))
    o = res.options
    assert set(o["type"]) == {"P"}
    assert o["delta"].abs().between(0.3, 0.6).all()
    assert (o["dte"] >= 50).all()


def test_wide_spread_flag_and_mid_hint():
    chain = _chain({30: FAIR + 0.02}, half_spread=0.08)
    res = analyze(_md(chain), manual(rich_threshold=3.0))
    rich = res.options[res.options["verdict"] == VERDICT_RICH]
    assert not rich.empty
    assert rich["alertes"].str.contains("vise le mid").all()
    assert res.options["alertes"].str.contains("Spread large").any()


def test_earnings_flag_adjustment_and_implied_move():
    move = 0.06
    earn = NOW.date() + dt.timedelta(days=20)

    def vol_after(T):
        return np.sqrt(FAIR**2 + move**2 / T)

    chain = _chain({10: FAIR, 30: vol_after, 60: vol_after})
    md = _md(chain, earnings=earn)
    res = analyze(md, manual())
    term = res.term.set_index("dte")
    assert not term.loc[10, "resultats_avant"] and term.loc[30, "resultats_avant"]
    assert res.summary["mouvement_resultats_implicite"] == pytest.approx(move, abs=0.003)
    assert res.options[res.options["dte"] == 30]["alertes"].str.contains("Résultats").all()
    assert set(res.options[res.options["dte"] == 30]["verdict"]) == {VERDICT_RICH}
    # avec le mouvement attendu intégré, la vol juste rejoint le marché
    res2 = analyze(md, manual(earnings_move=move))
    assert set(res2.options[res2.options["dte"] == 30]["verdict"]) == {VERDICT_NEUTRAL}


def test_without_history_needs_manual_vol():
    md = _md(_chain({30: FAIR}), history=False)
    with pytest.raises(ValueError):
        analyze(md, ScreenParams(fair_vol_model="garch"))
    res = analyze(md, manual())
    assert np.isnan(res.summary["rv_20j"])
    assert res.cone.empty


def test_atm_and_summary_fields():
    res = analyze(_md(_chain({30: 0.3, 90: 0.3})), manual())
    assert res.term["atm_iv"].values == pytest.approx([0.3, 0.3], abs=2e-3)
    assert res.summary["atm_iv_30j"] == pytest.approx(0.3, abs=2e-3)
    assert res.summary["ecart_30j_pts"] == pytest.approx(5.0, abs=0.3)


def test_demo_end_to_end():
    md = make_demo_market(now=NOW)
    res = analyze(md, ScreenParams(dte_min=14, dte_max=90, earnings_move=0.04))
    assert len(res.options) > 20
    assert {VERDICT_CHEAP, VERDICT_RICH} <= set(res.options["verdict"])
    assert res.summary["iv_rank"] >= 0
    assert not res.cone.empty
    # l'IV recalculée retrouve la vol utilisée pour générer la démo (options liquides)
    f = res.full[res.full["delta"].abs().between(0.1, 0.9)]
    assert np.nanmedian(np.abs(f["iv_mid"] - f["vendor_iv"])) < 0.003


def test_cli_on_demo(capsys, tmp_path):
    out = tmp_path / "res.csv"
    assert cli_main(["DEMO", "--source", "demo", "--type", "put", "--dte", "14-60",
                     "--export", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Structure par terme" in text and "verdict" in text
    assert set(pd.read_csv(out)["type"]) == {"P"}
