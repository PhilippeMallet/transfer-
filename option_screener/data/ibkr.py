"""Source Interactive Brokers via ``ib_async`` (TWS ou IB Gateway).

Prérequis sur le PC :
- TWS ou IB Gateway lancé et connecté ;
- API activée : Configuration > API > Settings > « Enable ActiveX and Socket
  Clients » ; port 7497 (TWS papier), 7496 (TWS réel), 4002/4001 (Gateway) ;
- « Read-Only API » peut rester coché : l'outil ne passe aucun ordre.

Symboles acceptés : ``AAPL``, ``SPY`` (actions/ETF, SMART), ``SPX``, ``NDX``,
``RUT`` (indices US), ``ESTX50``, ``DAX`` (Eurex), ``CAC40`` (Monep), ou la
forme explicite ``SYMBOLE:BOURSE:DEVISE`` (ex. ``ASML:AEB:EUR``).
"""

from __future__ import annotations

import datetime as dt
import math
import socket
import time

import numpy as np
import pandas as pd

from .base import DataProvider, ProviderError, standardize_history

INDEXES = {
    "SPX": ("CBOE", "USD"), "NDX": ("NASDAQ", "USD"), "RUT": ("RUSSELL", "USD"),
    "XSP": ("CBOE", "USD"), "VIX": ("CBOE", "USD"), "OEX": ("CBOE", "USD"),
    "DJX": ("CBOE", "USD"), "ESTX50": ("EUREX", "EUR"), "DAX": ("EUREX", "EUR"),
    "CAC40": ("MONEP", "EUR"), "SMI": ("EUREX", "CHF"),
}

YAHOO_EQUIV = {"SPX": "^SPX", "NDX": "^NDX", "RUT": "^RUT", "XSP": "^SPX", "VIX": "^VIX",
               "OEX": "^OEX", "DJX": "^DJI", "ESTX50": "^STOXX50E", "DAX": "^GDAXI",
               "CAC40": "^FCHI", "SMI": "^SSMI"}


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) and v != -1 else float("nan")


class IBKRProvider(DataProvider):
    name = "IBKR"

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 17,
                 delayed: bool = False, timeout: float = 6.0, batch_size: int = 90,
                 max_expiries: int = 12, max_strikes_per_expiry: int = 40,
                 ib=None, fallback: DataProvider | None = None):
        super().__init__(fallback=fallback)
        self.host, self.port, self.client_id = host, int(port), int(client_id)
        self.delayed = delayed
        self.timeout = timeout
        self.batch_size = min(int(batch_size), 95)
        self.max_expiries = max_expiries
        self.max_strikes = max_strikes_per_expiry
        self.ib = ib
        self._own_ib = ib is None
        self._underlying = None
        self._dividends = None

    # --- connexion ---------------------------------------------------------
    @classmethod
    def available(cls, host: str = "127.0.0.1", port: int = 7497, **_) -> tuple[bool, str]:
        try:
            import ib_async  # noqa: F401
        except ImportError:
            return False, "paquet ib_async non installé"
        try:
            with socket.create_connection((host, int(port)), timeout=0.5):
                return True, ""
        except OSError:
            return False, f"TWS / IB Gateway injoignable sur {host}:{port}"

    def connect(self):
        if self.ib is None:
            from ib_async import IB, util
            util.getLoop()  # crée une boucle asyncio si on est dans un thread (Streamlit)
            self.ib = IB()
        if not self.ib.isConnected():
            kwargs = dict(clientId=self.client_id, timeout=10, readonly=True)
            try:
                from ib_async.ib import StartupFetch
                kwargs["fetchFields"] = StartupFetch(0)  # pas besoin des positions/ordres
            except Exception:  # noqa: BLE001
                pass
            try:
                self.ib.connect(self.host, self.port, **kwargs)
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(
                    f"Connexion IBKR impossible ({self.host}:{self.port}, client {self.client_id}) : "
                    f"{exc}. TWS/Gateway est-il lancé avec l'API activée ?") from exc
        # 2 = temps réel, figé hors séance ; 4 = différé, figé hors séance
        self.ib.reqMarketDataType(4 if self.delayed else 2)

    def close(self):
        if self.ib is not None and self._own_ib:
            try:
                self.ib.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.ib = None

    # --- contrats ----------------------------------------------------------
    def _parse(self, ticker: str):
        parts = ticker.strip().upper().split(":")
        sym = parts[0]
        exch = parts[1] if len(parts) > 1 else None
        cur = parts[2] if len(parts) > 2 else None
        if sym in INDEXES:
            d_exch, d_cur = INDEXES[sym]
            return "IND", sym, exch or d_exch, cur or d_cur
        return "STK", sym, exch or "SMART", cur or "USD"

    def underlying(self, ticker: str):
        if self._underlying is not None and self._underlying[0] == ticker:
            return self._underlying[1]
        from ib_async import Index, Stock
        sec, sym, exch, cur = self._parse(ticker)
        contract = Index(sym, exch, cur) if sec == "IND" else Stock(sym, exch, cur)
        q = self.ib.qualifyContracts(contract)
        if not q or q[0] is None:
            raise ProviderError(f"Contrat IBKR introuvable pour {ticker}")
        self._underlying = (ticker, q[0])
        self.default_style = "european" if sec == "IND" else "american"
        self.currency = cur
        self.expiry_tz = "Europe/Paris" if cur in ("EUR", "CHF") else "America/New_York"
        return q[0]

    def fallback_ticker(self, ticker: str) -> str:
        sym = ticker.strip().upper().split(":")[0]
        return YAHOO_EQUIV.get(sym, sym)

    # --- données -----------------------------------------------------------
    def _wait(self, tickers):
        """Attend que ~90 % des tickers aient une cotation (les contrats
        inexistants pour une échéance ne répondent jamais), au plus
        ``timeout`` secondes."""
        deadline = time.time() + self.timeout
        need = max(1, int(math.ceil(0.9 * len(tickers))))
        while time.time() < deadline:
            self.ib.sleep(0.25)
            ready = sum(
                (np.isfinite(_num(t.bid)) and np.isfinite(_num(t.ask))) or np.isfinite(_num(t.close))
                for t in tickers
            )
            if ready >= need:
                self.ib.sleep(0.75)  # laisse arriver volume / OI / grecques
                return

    def spot(self, ticker: str) -> float:
        contract = self.underlying(ticker)
        generic = "456" if contract.secType == "STK" else ""
        t = self.ib.reqMktData(contract, generic, False, False)
        try:
            self._wait([t])
            price = _num(t.marketPrice())
            if not np.isfinite(price):
                price = _num(t.last)
            if not np.isfinite(price):
                price = _num(t.close)
            self._dividends = getattr(t, "dividends", None)
        finally:
            self.ib.cancelMktData(contract)
        if not np.isfinite(price):
            raise ProviderError(f"Pas de prix pour {ticker} (abonnement aux données ?)")
        return float(price)

    def _bars(self, contract, duration: str, what: str) -> pd.DataFrame:
        bars = self.ib.reqHistoricalData(contract, "", duration, "1 day", what, True, 1)
        if not bars:
            raise ProviderError(f"pas de données historiques ({what}) : abonnement manquant ?")
        return pd.DataFrame([{"date": b.date, "open": b.open, "high": b.high,
                              "low": b.low, "close": b.close} for b in bars])

    def history(self, ticker: str, years: int = 2) -> pd.DataFrame:
        contract = self.underlying(ticker)
        return standardize_history(self._bars(contract, f"{years} Y", "TRADES"))

    def iv_history(self, ticker: str) -> pd.Series:
        contract = self.underlying(ticker)
        df = standardize_history(self._bars(contract, "1 Y", "OPTION_IMPLIED_VOLATILITY"))
        return df["close"]

    def dividend_yield(self, ticker: str, spot: float) -> float:
        div = self._dividends
        amount = _num(getattr(div, "next12Months", None)) if div is not None else float("nan")
        if not np.isfinite(amount):
            amount = _num(getattr(div, "past12Months", None)) if div is not None else float("nan")
        if not np.isfinite(amount):
            if self.underlying(ticker).secType == "IND":
                raise NotImplementedError
            raise ProviderError("dividendes non reçus")
        return float(-np.log(max(1e-9, 1 - amount / spot)))

    def _select_expiries(self, expiries: list[dt.date]) -> list[dt.date]:
        if len(expiries) <= self.max_expiries:
            return expiries
        idx = np.unique(np.round(np.linspace(0, len(expiries) - 1, self.max_expiries)).astype(int))
        return [expiries[i] for i in idx]

    def chain_contracts(self, ticker: str, spot: float, dte_min: int, dte_max: int,
                        moneyness: float, today: dt.date | None = None):
        """Liste des contrats d'options à coter, déjà filtrés (DTE, strikes)."""
        from ib_async import Option
        und = self.underlying(ticker)
        today = today or dt.date.today()
        params = self.ib.reqSecDefOptParams(und.symbol, "", und.secType, und.conId)
        if not params:
            raise ProviderError(f"Pas de chaîne d'options IBKR pour {ticker}")
        wanted = "SMART" if und.secType == "STK" else und.exchange
        chosen = [p for p in params if p.exchange == wanted] or [params[0]]

        by_expiry: dict[dt.date, list] = {}
        for p in chosen:
            for e in p.expirations:
                d = dt.datetime.strptime(e[:8], "%Y%m%d").date()
                days = (d - today).days
                if dte_min <= days <= dte_max:
                    by_expiry.setdefault(d, []).append(p)
        contracts = []
        for d in self._select_expiries(sorted(by_expiry)):
            T = max((d - today).days, 1) / 365
            band = min(moneyness, max(0.05, 2.5 * 0.35 * math.sqrt(T)))
            for p in by_expiry[d]:
                strikes = np.array(sorted(s for s in p.strikes if abs(s / spot - 1) <= band))
                if strikes.size > self.max_strikes:
                    strikes = strikes[np.argsort(np.abs(strikes - spot))[:self.max_strikes]]
                for k in sorted(strikes):
                    for right in ("C", "P"):
                        contracts.append(Option(
                            und.symbol, d.strftime("%Y%m%d"), float(k), right,
                            "SMART" if und.secType == "STK" else p.exchange,
                            multiplier=str(p.multiplier), currency=und.currency,
                            tradingClass=p.tradingClass))
        return contracts

    def chain(self, ticker: str, spot: float, dte_min: int, dte_max: int,
              moneyness: float) -> pd.DataFrame:
        contracts = self.chain_contracts(ticker, spot, dte_min, dte_max, moneyness)
        rows = []
        for i in range(0, len(contracts), self.batch_size):
            batch = contracts[i:i + self.batch_size]
            tickers = [self.ib.reqMktData(c, "101", False, False) for c in batch]
            try:
                self._wait(tickers)
                for c, t in zip(batch, tickers):
                    rows.append(self._row(c, t))
            finally:
                for c in batch:
                    self.ib.cancelMktData(c)
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        quoted = df["bid"].notna() | df["ask"].notna()
        return df[quoted].reset_index(drop=True)

    def _row(self, c, t) -> dict:
        oi = float("nan")
        for attr in ("openInterest", "callOpenInterest" if c.right == "C" else "putOpenInterest"):
            v = _num(getattr(t, attr, None))
            if np.isfinite(v) and v >= 0:
                oi = v
                break
        greeks = getattr(t, "modelGreeks", None)
        iv = _num(getattr(greeks, "impliedVol", None)) if greeks is not None else float("nan")
        bid, ask = _num(t.bid), _num(t.ask)
        return {
            "expiry": dt.datetime.strptime(c.lastTradeDateOrContractMonth[:8], "%Y%m%d").date(),
            "strike": float(c.strike), "type": c.right,
            "bid": bid if np.isfinite(bid) and bid >= 0 else np.nan,
            "ask": ask if np.isfinite(ask) and ask > 0 else np.nan,
            "last": _num(t.last), "volume": _num(t.volume), "open_interest": oi,
            "vendor_iv": iv, "style": self.default_style,
            "multiplier": _num(c.multiplier) if c.multiplier else 100.0,
        }
