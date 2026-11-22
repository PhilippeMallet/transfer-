"""Interface commune des sources de données.

Chaque source implémente quelques méthodes élémentaires (spot, historique,
chaîne...). ``DataProvider.load`` les assemble dans un ``MarketData`` et,
quand une méthode échoue ou n'est pas fournie par la source, essaie la source
de secours (Yahoo par défaut) et note ce qui s'est passé.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

CHAIN_COLUMNS = ["expiry", "strike", "type", "bid", "ask", "last", "volume",
                 "open_interest", "vendor_iv", "style", "multiplier"]


@dataclass
class MarketData:
    ticker: str
    spot: float
    chain: pd.DataFrame                       # colonnes CHAIN_COLUMNS
    history: pd.DataFrame | None = None       # open/high/low/close indexé par date
    rate: float | None = None                 # taux sans risque continu
    div_yield: float | None = None            # rendement de dividende continu
    iv_history: pd.Series | None = None       # IV ATM ~30 j historique (décimal)
    earnings_date: dt.date | None = None
    style: str = "american"                   # style par défaut si absent de la chaîne
    currency: str = "USD"
    source: str = ""
    asof: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    expiry_tz: str = "America/New_York"
    expiry_hour: int = 16
    notes: list[str] = field(default_factory=list)


class ProviderError(RuntimeError):
    pass


class DataProvider:
    """Classe de base. Les sous-classes surchargent les méthodes qu'elles
    savent servir ; les autres lèvent NotImplementedError et passent au
    secours."""

    name = "base"
    default_style = "american"
    currency = "USD"
    expiry_tz = "America/New_York"

    def __init__(self, fallback: "DataProvider | None" = None):
        self.fallback = fallback
        self.provider_notes: list[str] = []  # remarques ajoutées par la source pendant load()

    # --- disponibilité -----------------------------------------------------
    @classmethod
    def available(cls, **kwargs) -> tuple[bool, str]:
        return True, ""

    # --- méthodes élémentaires --------------------------------------------
    def connect(self):
        pass

    def close(self):
        pass

    def spot(self, ticker: str) -> float:
        raise NotImplementedError

    def history(self, ticker: str, years: int = 2) -> pd.DataFrame:
        raise NotImplementedError

    def chain(self, ticker: str, spot: float, dte_min: int, dte_max: int,
              moneyness: float) -> pd.DataFrame:
        raise NotImplementedError

    def iv_history(self, ticker: str) -> pd.Series:
        raise NotImplementedError

    def rate(self) -> float:
        raise NotImplementedError

    def dividend_yield(self, ticker: str, spot: float) -> float:
        raise NotImplementedError

    def earnings_date(self, ticker: str) -> dt.date | None:
        raise NotImplementedError

    def fallback_ticker(self, ticker: str) -> str:
        """Symbole à utiliser chez la source de secours (ex. 'AAPL US Equity'
        -> 'AAPL')."""
        return ticker

    # --- assemblage --------------------------------------------------------
    def _try(self, notes, label, fn, *args, use_fallback=True, fb_args=None):
        try:
            return fn(*args)
        except NotImplementedError:
            err = None
        except Exception as exc:  # noqa: BLE001 - on veut continuer
            err = exc
        if use_fallback and self.fallback is not None:
            fb_fn = getattr(self.fallback, fn.__name__)
            try:
                value = fb_fn(*(fb_args if fb_args is not None else args))
                msg = f"{label} : repris sur {self.fallback.name}"
                if err is not None:
                    msg += f" ({self.name} : {err})"
                notes.append(msg)
                return value
            except NotImplementedError:
                pass
            except Exception as exc2:  # noqa: BLE001
                notes.append(f"{label} indisponible ({self.name} : {err or 'non fourni'} ; "
                             f"{self.fallback.name} : {exc2})")
                return None
        if err is not None:
            notes.append(f"{label} indisponible ({self.name} : {err})")
        return None

    def load(self, ticker: str, dte_min: int = 0, dte_max: int = 120,
             moneyness: float = 0.30, years: int = 2) -> MarketData:
        notes: list[str] = []
        fb = self.fallback_ticker(ticker)
        self.connect()
        try:
            spot = self.spot(ticker)
            if not (spot and spot > 0):
                raise ProviderError(f"Spot introuvable pour {ticker}")
            chain = self.chain(ticker, spot, dte_min, dte_max, moneyness)
            if chain is None or chain.empty:
                raise ProviderError(f"Aucune option trouvée pour {ticker} entre "
                                    f"{dte_min} et {dte_max} jours")
            chain = normalize_chain(chain, self.default_style)
            history = self._try(notes, "Historique du sous-jacent", self.history, ticker, years,
                                fb_args=(fb, years))
            iv_hist = self._try(notes, "Historique de vol implicite", self.iv_history, ticker,
                                use_fallback=False)
            rate = self._try(notes, "Taux sans risque", self.rate, fb_args=())
            div = self._try(notes, "Dividende", self.dividend_yield, ticker, spot,
                            fb_args=(fb, spot))
            earn = self._try(notes, "Date de résultats", self.earnings_date, ticker,
                             fb_args=(fb,))
        finally:
            self.close()
        notes = self.provider_notes + notes
        return MarketData(
            ticker=ticker, spot=float(spot), chain=chain, history=history, rate=rate,
            div_yield=div, iv_history=iv_hist, earnings_date=earn, style=self.default_style,
            currency=self.currency, source=self.name, expiry_tz=self.expiry_tz, notes=notes,
        )


def normalize_chain(chain: pd.DataFrame, default_style: str = "american") -> pd.DataFrame:
    """Met une chaîne au format standard : colonnes CHAIN_COLUMNS, type 'C'/'P',
    expiry en date, nombres en float."""
    df = chain.copy()
    for col in CHAIN_COLUMNS:
        if col not in df.columns:
            df[col] = float("nan")
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    df["type"] = df["type"].astype(str).str.strip().str.upper().str[0]
    df = df[df["type"].isin(["C", "P"])]
    for col in ("strike", "bid", "ask", "last", "volume", "open_interest", "vendor_iv",
                "multiplier"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["multiplier"] = df["multiplier"].fillna(100.0)
    df["style"] = df["style"].where(df["style"].isin(["american", "european"]), default_style)
    df = df.dropna(subset=["strike", "expiry"])
    return df[CHAIN_COLUMNS].sort_values(["expiry", "type", "strike"]).reset_index(drop=True)


def standardize_history(df: pd.DataFrame) -> pd.DataFrame:
    """Renomme les colonnes OHLC en minuscules, index en dates, trie."""
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    if "date" in out.columns:
        out = out.set_index("date")
    if "adj close" in out.columns and "close" not in out.columns:
        out["close"] = out["adj close"]
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    if "close" not in out.columns:
        raise ProviderError("Historique sans colonne 'close'")
    for col in ("open", "high", "low"):
        if col not in out.columns:
            out[col] = out["close"]
    out = out[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    out = out[out["close"] > 0].dropna()
    return out[~out.index.duplicated(keep="last")].sort_index()
