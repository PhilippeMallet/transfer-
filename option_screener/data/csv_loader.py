"""Import d'une chaîne d'options depuis un fichier CSV (export broker,
Bloomberg/Excel, saisie manuelle...).

Colonnes reconnues (noms français ou anglais, casse et accents ignorés) :
- échéance : expiry, expiration, echeance, maturity...
- strike : strike, prix d'exercice...
- type : type, right, call/put, cp (valeurs C/P/Call/Put)
- bid, ask (obligatoires), last, volume, open_interest/oi (facultatifs)
- spot (facultatif : sinon à saisir dans l'appli)
À défaut d'échéance/strike/type, un symbole OCC (``AAPL251219C00200000``)
dans une colonne contract/symbol est décodé.

Séparateur , ; ou tabulation, décimale point ou virgule : détectés.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata

import numpy as np
import pandas as pd

from .base import DataProvider, ProviderError, standardize_history

ALIASES = {
    "expiry": ["expiry", "expiration", "expiration date", "expiry date", "echeance", "maturity",
               "maturite", "exp", "date d expiration", "expirationdate", "last trade date"],
    "strike": ["strike", "strike price", "prix d exercice", "k", "exercice"],
    "type": ["type", "right", "call put", "put call", "cp", "pc", "option type", "callput",
             "call ou put"],
    "bid": ["bid", "bid price", "achat", "prix acheteur"],
    "ask": ["ask", "ask price", "offer", "vente", "prix vendeur"],
    "last": ["last", "last price", "lastprice", "dernier", "dernier prix"],
    "volume": ["volume", "vol", "volume du jour"],
    "open_interest": ["open interest", "open_interest", "oi", "openinterest", "positions ouvertes"],
    "style": ["style", "exercise style", "style d exercice"],
    "spot": ["spot", "underlying", "underlying price", "sous jacent", "prix du sous jacent"],
    "contract": ["contract", "contract symbol", "contractsymbol", "symbol", "symbole", "ticker"],
}
OCC_RE = re.compile(r"([A-Z.]{1,6})\s*(\d{6})([CP])(\d{8})")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[_\-/'.]", " ", s.lower())
    return " ".join(s.split())


def _to_float(series: pd.Series) -> pd.Series:
    if not pd.api.types.is_numeric_dtype(series):
        series = (series.astype(str).str.replace(" ", "", regex=False)
                  .str.replace(" ", "", regex=False).str.replace(",", ".", regex=False)
                  .str.replace("%", "", regex=False))
    return pd.to_numeric(series, errors="coerce")


def read_table(source) -> pd.DataFrame:
    """Lit un CSV (chemin, bytes ou fichier) en détectant séparateur et décimale."""
    if hasattr(source, "read"):
        raw = source.read()
    elif isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
    else:
        with open(source, "rb") as fh:
            raw = fh.read()
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
    first = text.splitlines()[0] if text else ""
    sep = ";" if first.count(";") > first.count(",") else "\t" if "\t" in first else ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str)


def parse_chain(df: pd.DataFrame, dayfirst: bool = False) -> tuple[pd.DataFrame, float | None]:
    """Convertit un tableau brut en chaîne standard. Renvoie (chaîne, spot ou None)."""
    lookup = {_norm(c): c for c in df.columns}
    cols = {}
    for key, names in ALIASES.items():
        for n in names:
            if _norm(n) in lookup:
                cols[key] = lookup[_norm(n)]
                break
    out = pd.DataFrame(index=df.index)
    if not {"expiry", "strike", "type"} <= cols.keys():
        if "contract" not in cols:
            raise ProviderError("Colonnes échéance / strike / type introuvables (ni symbole OCC).")
        decoded = df[cols["contract"]].astype(str).str.upper().str.extract(OCC_RE)
        if decoded.isna().all().all():
            raise ProviderError("Colonnes échéance / strike / type introuvables (ni symbole OCC).")
        out["expiry"] = pd.to_datetime(decoded[1], format="%y%m%d", errors="coerce")
        out["type"] = decoded[2]
        out["strike"] = pd.to_numeric(decoded[3], errors="coerce") / 1000
    else:
        exp = df[cols["expiry"]].astype(str).str.strip()
        parsed = pd.to_datetime(exp, format="ISO8601", errors="coerce")
        if parsed.isna().mean() > 0.5:
            parsed = pd.to_datetime(exp, dayfirst=dayfirst, errors="coerce", format="mixed")
        out["expiry"] = parsed
        out["strike"] = _to_float(df[cols["strike"]])
        out["type"] = df[cols["type"]].astype(str).str.strip().str.upper().str[0]
    for key in ("bid", "ask"):
        if key not in cols:
            raise ProviderError(f"Colonne obligatoire manquante : {key}")
    for key in ("bid", "ask", "last", "volume", "open_interest"):
        out[key] = _to_float(df[cols[key]]) if key in cols else np.nan
    if "style" in cols:
        st = df[cols["style"]].astype(str).str.lower()
        out["style"] = np.where(st.str.startswith("eu"), "european",
                                np.where(st.str.startswith("am"), "american", None))
    out = out.dropna(subset=["expiry", "strike"])
    out["expiry"] = out["expiry"].dt.date
    spot = None
    if "spot" in cols:
        s = _to_float(df[cols["spot"]]).dropna()
        spot = float(s.iloc[0]) if len(s) else None
    return out.reset_index(drop=True), spot


class CSVProvider(DataProvider):
    """Chaîne lue dans un CSV ; historique optionnel (CSV ou source de secours)."""

    name = "CSV"

    def __init__(self, chain_file, spot: float | None = None, history_file=None,
                 style: str = "american", dayfirst: bool = False, expiry_tz: str = "America/New_York",
                 fallback: DataProvider | None = None):
        super().__init__(fallback=fallback)
        self._chain, csv_spot = parse_chain(read_table(chain_file), dayfirst=dayfirst)
        self._spot = spot or csv_spot
        self._history = standardize_history(read_table(history_file)) if history_file is not None else None
        self.default_style = style
        self.expiry_tz = expiry_tz

    def spot(self, ticker: str) -> float:
        if self._spot:
            return float(self._spot)
        raise ProviderError("Spot absent du CSV : renseigne-le dans l'appli.")

    def chain(self, ticker, spot, dte_min, dte_max, moneyness) -> pd.DataFrame:
        today = dt.date.today()
        days = pd.Series([(e - today).days for e in self._chain["expiry"]], index=self._chain.index)
        return self._chain[(days >= dte_min) & (days <= dte_max)]

    def history(self, ticker: str, years: int = 2) -> pd.DataFrame:
        if self._history is None:
            raise NotImplementedError
        return self._history
