"""Source Yahoo Finance via ``yfinance`` : gratuite, différée (~15 min),
surtout utile pour les sous-jacents US. Sert aussi de secours aux autres
sources pour l'historique, le taux, les dividendes et la date de résultats.

Hors séance, Yahoo renvoie souvent bid = ask = 0 : l'option
``use_last_if_no_quote`` remplace alors bid/ask par le dernier prix (à
prendre avec prudence, ce prix peut être ancien).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .base import DataProvider, ProviderError, standardize_history


class YahooProvider(DataProvider):
    name = "Yahoo"

    def __init__(self, use_last_if_no_quote: bool = False, fallback: DataProvider | None = None):
        super().__init__(fallback=fallback)
        self.use_last_if_no_quote = use_last_if_no_quote
        self._tickers: dict = {}

    @classmethod
    def available(cls, **_) -> tuple[bool, str]:
        try:
            import yfinance  # noqa: F401
            return True, ""
        except ImportError:
            return False, "paquet yfinance non installé"

    def _t(self, ticker: str):
        import yfinance as yf
        key = ticker.upper()
        if key not in self._tickers:
            self._tickers[key] = yf.Ticker(key)
        return self._tickers[key]

    def _configure(self, ticker: str):
        t = ticker.upper()
        self.default_style = "european" if t.startswith("^") else "american"
        if any(t.endswith(s) for s in (".PA", ".DE", ".AS", ".MI", ".MC", ".BR", ".SW")):
            self.expiry_tz, self.currency = "Europe/Paris", "EUR"
        else:
            self.expiry_tz, self.currency = "America/New_York", "USD"

    def spot(self, ticker: str) -> float:
        self._configure(ticker)
        t = self._t(ticker)
        price = float("nan")
        try:
            price = float(t.fast_info["last_price"])
        except Exception:  # noqa: BLE001
            pass
        if not np.isfinite(price) or price <= 0:
            h = t.history(period="5d")
            if h.empty:
                raise ProviderError(f"Ticker Yahoo inconnu ou sans cotation : {ticker}")
            price = float(h["Close"].iloc[-1])
        return price

    def history(self, ticker: str, years: int = 2) -> pd.DataFrame:
        h = self._t(ticker).history(period=f"{years}y", auto_adjust=False)
        if h.empty:
            raise ProviderError(f"historique Yahoo vide pour {ticker}")
        return standardize_history(h)

    def rate(self) -> float:
        h = self._t("^IRX").history(period="10d")
        if h.empty:
            raise ProviderError("^IRX indisponible")
        return float(np.log1p(h["Close"].dropna().iloc[-1] / 100.0))

    def dividend_yield(self, ticker: str, spot: float) -> float:
        if ticker.startswith("^"):
            return 0.0  # indice : le forward implicite (parité call-put) s'en charge
        div = self._t(ticker).dividends
        if div is None or len(div) == 0:
            return 0.0
        idx = pd.to_datetime(div.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        last_year = div[idx >= pd.Timestamp.today() - pd.Timedelta(days=365)]
        return float(-np.log(max(1e-9, 1 - last_year.sum() / spot)))

    def earnings_date(self, ticker: str):
        if ticker.startswith("^"):
            return None
        cal = self._t(ticker).calendar
        dates = []
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            dates = list(cal.loc["Earnings Date"].values)
        today = dt.date.today()
        future = sorted(pd.Timestamp(d).date() for d in dates if pd.Timestamp(d).date() >= today)
        return future[0] if future else None

    def chain(self, ticker: str, spot: float, dte_min: int, dte_max: int,
              moneyness: float) -> pd.DataFrame:
        t = self._t(ticker)
        today = dt.date.today()
        expiries = [dt.date.fromisoformat(e) for e in (t.options or ())]
        expiries = [e for e in expiries if dte_min <= (e - today).days <= dte_max]
        frames = []
        for e in expiries:
            oc = t.option_chain(e.isoformat())
            for df, right in ((oc.calls, "C"), (oc.puts, "P")):
                if df is None or df.empty:
                    continue
                f = pd.DataFrame({
                    "expiry": e, "strike": df["strike"], "type": right,
                    "bid": df["bid"], "ask": df["ask"], "last": df["lastPrice"],
                    "volume": df.get("volume"), "open_interest": df.get("openInterest"),
                    "style": self.default_style, "multiplier": 100.0,
                })
                frames.append(f[(f["strike"] / spot - 1).abs() <= moneyness])
        if not frames:
            return pd.DataFrame()
        chain = pd.concat(frames, ignore_index=True)
        no_quote = (chain["bid"].fillna(0) <= 0) & (chain["ask"].fillna(0) <= 0)
        if no_quote.mean() > 0.5:
            self.provider_notes.append(
                f"{no_quote.mean():.0%} des options sans bid/ask : marché fermé ? "
                + ("Le dernier prix est utilisé à la place (peut être ancien)."
                   if self.use_last_if_no_quote else
                   "Coche « utiliser le dernier prix hors séance » ou réessaie pendant la séance."))
        if self.use_last_if_no_quote:
            m = no_quote & (chain["last"] > 0)
            chain.loc[m, "bid"] = chain.loc[m, "last"]
            chain.loc[m, "ask"] = chain.loc[m, "last"]
        else:
            chain.loc[no_quote, ["bid", "ask"]] = np.nan
        return chain
