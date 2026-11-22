"""Graphiques Plotly de l'appli (sans dépendance à Streamlit).

Palette catégorielle à 3 couleurs (bleu, orange, aqua), validée en clair et
en sombre (séparation daltonisme, contraste). L'aqua est sous 3:1 sur fond
clair : il est toujours accompagné d'une légende et d'un tableau.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

PALETTES = {
    "light": {"s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "band": "rgba(42,120,214,0.16)",
              "band2": "rgba(42,120,214,0.30)", "muted": "#898781", "grid": "#e1e0d9",
              "text": "#0b0b0b", "text2": "#52514e"},
    "dark": {"s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "band": "rgba(57,135,229,0.18)",
             "band2": "rgba(57,135,229,0.34)", "muted": "#898781", "grid": "#2c2c2a",
             "text": "#f0efec", "text2": "#c3c2b7"},
}
VERDICT_COLORS = {
    "light": {"Bon marché": ("#0ca30c", "rgba(12,163,12,0.14)"),
              "Cher": ("#d03b3b", "rgba(208,59,59,0.14)"),
              "Neutre": ("#898781", "rgba(137,135,129,0.10)")},
    "dark": {"Bon marché": ("#0ca30c", "rgba(12,163,12,0.22)"),
             "Cher": ("#e66767", "rgba(208,59,59,0.24)"),
             "Neutre": ("#898781", "rgba(137,135,129,0.16)")},
}


def _layout(fig: go.Figure, mode: str, title: str, ytitle: str, xtitle: str = ""):
    c = PALETTES[mode]
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15, color=c["text"])),
        margin=dict(l=10, r=10, t=50, b=10), height=380,
        hovermode="x unified", legend=dict(orientation="h", y=1.02, x=1, xanchor="right",
                                           yanchor="bottom", font=dict(color=c["text2"])),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', color=c["text2"]),
    )
    fig.update_xaxes(title=xtitle, showgrid=False, linecolor=c["grid"], tickfont=dict(color=c["muted"]))
    fig.update_yaxes(title=ytitle, gridcolor=c["grid"], zeroline=False, tickfont=dict(color=c["muted"]),
                     ticksuffix=" %")
    return fig


def term_structure(term: pd.DataFrame, earnings_date=None, mode: str = "light") -> go.Figure:
    """IV ATM et vol juste par échéance."""
    c = PALETTES[mode]
    t = term.sort_values("dte")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t["dte"], y=t["atm_iv"] * 100, name="IV ATM (marché)", mode="lines+markers",
        line=dict(color=c["s1"], width=2), marker=dict(size=8),
        customdata=np.stack([t["expiry"].astype(str)], axis=-1),
        hovertemplate="%{customdata[0]} · %{x} j<br>IV ATM %{y:.1f} %<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=t["dte"], y=t["vol_juste"] * 100, name="Vol juste (modèle)", mode="lines+markers",
        line=dict(color=c["s2"], width=2), marker=dict(size=8, symbol="diamond"),
        hovertemplate="Vol juste %{y:.1f} %<extra></extra>"))
    if earnings_date is not None and t["resultats_avant"].any():
        first_after = t[t["resultats_avant"]]["dte"].min()
        fig.add_vline(x=first_after - 0.5, line=dict(color=c["muted"], width=1, dash="dot"),
                      annotation_text=f"résultats {earnings_date:%d/%m}", annotation_position="top left",
                      annotation_font_color=c["muted"])
    return _layout(fig, mode, "Structure par terme", "Volatilité", "Jours avant échéance")


def smile(full: pd.DataFrame, expiry, forward: float, mode: str = "light") -> go.Figure:
    """Smile d'une échéance : fourchette IV bid/ask, IV mid et vol juste.
    Côté OTM uniquement (puts sous le forward, calls au-dessus)."""
    c = PALETTES[mode]
    g = full[full["expiry"] == expiry]
    g = g[((g["type"] == "C") & (g["strike"] >= forward)) | ((g["type"] == "P") & (g["strike"] < forward))]
    g = g[np.isfinite(g["iv_mid"])].sort_values("strike")
    fig = go.Figure()
    if g.empty:
        return _layout(fig, mode, "Smile : pas de données", "Volatilité", "Strike")
    ask = g["iv_ask"] * 100
    bid = g["iv_bid"].fillna(g["iv_mid"]) * 100
    fig.add_trace(go.Scatter(x=g["strike"], y=ask, mode="lines", line=dict(width=0), showlegend=False,
                             hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=g["strike"], y=bid, mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=c["band"], name="Fourchette IV bid / ask", hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=g["strike"], y=g["iv_mid"] * 100, mode="lines+markers", name="IV mid",
        line=dict(color=c["s1"], width=2), marker=dict(size=8),
        customdata=np.stack([g["type"], bid, ask], axis=-1),
        hovertemplate="Strike %{x} (%{customdata[0]})<br>IV mid %{y:.1f} %"
                      "<br>bid %{customdata[1]:.1f} % · ask %{customdata[2]:.1f} %<extra></extra>"))
    fv = float(g["vol_juste"].iloc[0]) * 100
    fig.add_trace(go.Scatter(x=[g["strike"].min(), g["strike"].max()], y=[fv, fv], mode="lines",
                             name=f"Vol juste {fv:.1f} %", line=dict(color=c["s2"], width=2),
                             hoverinfo="skip"))
    fig.add_vline(x=forward, line=dict(color=c["muted"], width=1, dash="dot"),
                  annotation_text="forward", annotation_position="top", annotation_font_color=c["muted"])
    return _layout(fig, mode, f"Smile au {expiry:%d/%m/%Y}", "Volatilité", "Strike")


def cone(cone_df: pd.DataFrame, term: pd.DataFrame, mode: str = "light") -> go.Figure:
    """Cône de vol réalisée (min-max, 25-75 %, médiane) avec la vol réalisée
    actuelle et l'IV ATM des échéances placée à l'horizon correspondant."""
    c = PALETTES[mode]
    fig = go.Figure()
    if cone_df is None or cone_df.empty:
        return _layout(fig, mode, "Cône de vol : historique insuffisant", "Volatilité")
    x = cone_df["fenetre"]
    pct = {k: cone_df[k] * 100 for k in ("min", "p25", "mediane", "p75", "max", "actuelle")}
    fig.add_trace(go.Scatter(x=x, y=pct["max"], mode="lines", line=dict(width=0), showlegend=False,
                             hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=pct["min"], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=c["band"], name="Min – max historique",
                             customdata=np.stack([pct["min"], pct["max"]], axis=-1),
                             hovertemplate="min %{customdata[0]:.1f} % · max %{customdata[1]:.1f} %<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=pct["p75"], mode="lines", line=dict(width=0), showlegend=False,
                             hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=pct["p25"], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=c["band2"], name="25 – 75 %",
                             customdata=np.stack([pct["p25"], pct["p75"]], axis=-1),
                             hovertemplate="25 % : %{customdata[0]:.1f} % · 75 % : %{customdata[1]:.1f} %<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=pct["mediane"], mode="lines", name="Médiane",
                             line=dict(color=c["s1"], width=2),
                             hovertemplate="médiane %{y:.1f} %<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=pct["actuelle"], mode="lines+markers", name="Vol réalisée actuelle",
                             line=dict(color=c["s2"], width=2), marker=dict(size=8),
                             hovertemplate="actuelle %{y:.1f} %<extra></extra>"))
    t = term[np.isfinite(term["atm_iv"])]
    if not t.empty:
        fig.add_trace(go.Scatter(x=t["T"] * 252, y=t["atm_iv"] * 100, mode="markers", name="IV ATM (marché)",
                                 marker=dict(color=c["s3"], size=10, symbol="diamond",
                                             line=dict(width=2, color="rgba(0,0,0,0)")),
                                 customdata=np.stack([t["expiry"].astype(str)], axis=-1),
                                 hovertemplate="IV ATM %{customdata[0]} : %{y:.1f} %<extra></extra>"))
    _layout(fig, mode, "Cône de volatilité (2 ans)", "Volatilité", "Horizon (séances)")
    fig.update_layout(hovermode="closest")
    return fig
