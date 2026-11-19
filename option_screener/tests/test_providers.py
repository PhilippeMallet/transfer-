import datetime as dt
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from option_screener.data.base import DataProvider, normalize_chain, standardize_history
from option_screener.data.bloomberg import (BloombergProvider, normalize_ticker,
                                            parse_option_ticker)
from option_screener.data.csv_loader import CSVProvider, parse_chain, read_table
from option_screener.data.demo import simulate_history
from option_screener.data.ibkr import IBKRProvider
from option_screener.pricing import bs_price

TODAY = dt.date.today()


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

def test_normalize_chain_and_history():
    raw = pd.DataFrame({"expiry": ["2030-01-18"], "strike": ["100"], "type": ["call"],
                        "bid": [1.0], "ask": [1.2]})
    c = normalize_chain(raw, "european")
    assert c.loc[0, "type"] == "C" and c.loc[0, "style"] == "european"
    assert c.loc[0, "multiplier"] == 100 and c.loc[0, "expiry"] == dt.date(2030, 1, 18)
    h = pd.DataFrame({"Date": ["2024-01-03", "2024-01-02"], "Close": [10, 11]})
    sh = standardize_history(h)
    assert list(sh.columns) == ["open", "high", "low", "close"]
    assert sh.index.is_monotonic_increasing


class _Hist(DataProvider):
    name = "Secours"

    def history(self, ticker, years=2):
        return simulate_history(n_days=300)

    def rate(self):
        return 0.042


class _Broken(DataProvider):
    name = "Principale"

    def spot(self, ticker):
        return 100.0

    def chain(self, ticker, spot, dte_min, dte_max, moneyness):
        exp = TODAY + dt.timedelta(days=30)
        return pd.DataFrame({"expiry": [exp, exp], "strike": [100, 100], "type": ["C", "P"],
                             "bid": [3.0, 2.8], "ask": [3.2, 3.0]})

    def history(self, ticker, years=2):
        raise RuntimeError("pas d'abonnement")


def test_load_uses_fallback_and_notes():
    md = _Broken(fallback=_Hist()).load("XYZ")
    assert md.history is not None and len(md.history) == 300
    assert md.rate == 0.042
    assert any("repris sur Secours" in n and "pas d'abonnement" in n for n in md.notes)
    assert md.iv_history is None


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def test_csv_french_export(tmp_path):
    exp = (TODAY + dt.timedelta(days=40)).strftime("%d/%m/%Y")
    text = (f"Échéance;Prix d'exercice;Call/Put;Achat;Vente;Positions ouvertes;Sous-jacent\n"
            f"{exp};100,0;Call;3,10;3,30;1 200;101,5\n"
            f"{exp};100,0;Put;2,40;2,55;800;101,5\n")
    f = tmp_path / "chaine.csv"
    f.write_text(text, encoding="utf-8")
    chain, spot = parse_chain(read_table(f), dayfirst=True)
    assert spot == 101.5
    assert list(chain["type"]) == ["C", "P"]
    assert chain["bid"].tolist() == [3.10, 2.40]
    assert chain["open_interest"].tolist() == [1200, 800]
    assert chain["expiry"].iloc[0] == TODAY + dt.timedelta(days=40)
    md = CSVProvider(f, dayfirst=True).load("XYZ")
    assert md.spot == 101.5 and len(md.chain) == 2


def test_csv_occ_symbols():
    exp = TODAY + dt.timedelta(days=60)
    sym = f"AAPL{exp:%y%m%d}C00195000"
    df = pd.DataFrame({"contractSymbol": [sym], "bid": ["4.1"], "ask": ["4.3"]})
    chain, _ = parse_chain(df)
    assert chain.loc[0, "strike"] == 195.0 and chain.loc[0, "type"] == "C"
    assert chain.loc[0, "expiry"] == exp


def test_csv_missing_columns():
    with pytest.raises(Exception):
        parse_chain(pd.DataFrame({"foo": [1], "bid": [1], "ask": [2]}))


# ---------------------------------------------------------------------------
# IBKR (faux client)
# ---------------------------------------------------------------------------

class FakeTicker(SimpleNamespace):
    def marketPrice(self):
        return self.last


class FakeIB:
    def __init__(self, history_ok=True):
        self.active = set()
        self.max_active = 0
        self.history_ok = history_ok
        self.exp_ok = (TODAY + dt.timedelta(days=30)).strftime("%Y%m%d")
        self.exp_far = (TODAY + dt.timedelta(days=200)).strftime("%Y%m%d")
        self.data_type = None

    def isConnected(self):
        return True

    def reqMarketDataType(self, t):
        self.data_type = t

    def sleep(self, s):
        pass

    def qualifyContracts(self, *cs):
        for c in cs:
            c.conId = 1
        return list(cs)

    def reqSecDefOptParams(self, sym, fut_exch, sec_type, con_id):
        strikes = [float(k) for k in range(50, 151, 1)]
        return [SimpleNamespace(exchange="SMART", underlyingConId=1, tradingClass=sym, multiplier="100",
                                expirations=[self.exp_ok, self.exp_far], strikes=strikes)]

    def reqMktData(self, c, generic="", snapshot=False, regulatory=False):
        self.active.add(id(c))
        self.max_active = max(self.max_active, len(self.active))
        if c.secType in ("STK", "IND"):
            return FakeTicker(bid=99.9, ask=100.1, last=100.0, close=99.0,
                              dividends=SimpleNamespace(next12Months=1.0, past12Months=0.9))
        T = 30 / 365
        if c.strike % 5:  # strikes non listés pour cette échéance : pas de cotation
            nan = float("nan")
            return FakeTicker(bid=nan, ask=nan, last=nan, close=nan, volume=nan,
                              callOpenInterest=nan, putOpenInterest=nan, modelGreeks=None)
        px = float(bs_price(100, c.strike, T, 0.04, 0.01, 0.3, c.right == "C"))
        return FakeTicker(bid=round(px * 0.98, 2), ask=round(px * 1.02, 2) + 0.01, last=px, close=px,
                          volume=10, callOpenInterest=500 if c.right == "C" else float("nan"),
                          putOpenInterest=400 if c.right == "P" else float("nan"),
                          modelGreeks=SimpleNamespace(impliedVol=0.3))

    def cancelMktData(self, c):
        self.active.discard(id(c))

    def reqHistoricalData(self, c, end, duration, bar, what, rth, fmt=1):
        if not self.history_ok:
            return []
        h = simulate_history(n_days=300) if what == "TRADES" else simulate_history(n_days=250) * 0 + 0.3
        return [SimpleNamespace(date=d.date(), open=r.open, high=r.high, low=r.low, close=r.close)
                for d, r in h.iterrows()]

    def disconnect(self):
        pass


def test_ibkr_chain_filters_batches_and_parses():
    ib = FakeIB()
    prov = IBKRProvider(ib=ib, timeout=0.05, batch_size=90, max_strikes_per_expiry=40)
    md = prov.load("AAPL", dte_min=7, dte_max=60, moneyness=0.3)
    assert ib.data_type == 2
    assert ib.max_active <= 90
    c = md.chain
    assert set(c["expiry"]) == {TODAY + dt.timedelta(days=30)}           # l'échéance lointaine est exclue
    assert (c["strike"] % 5 == 0).all()                                     # strikes sans cotation retirés
    assert c["strike"].between(70, 130).all()
    assert c["open_interest"].notna().all() and (c["vendor_iv"] == 0.3).all()
    assert md.style == "american"
    assert md.div_yield == pytest.approx(0.01, abs=1e-3)
    assert len(md.history) == 300 and md.iv_history.iloc[-1] == pytest.approx(0.3)


def test_ibkr_history_falls_back(monkeypatch):
    prov = IBKRProvider(ib=FakeIB(history_ok=False), timeout=0.05, delayed=True, fallback=_Hist())
    md = prov.load("SPX", dte_min=7, dte_max=60)
    assert md.style == "european"
    assert len(md.history) == 300
    assert any("Historique du sous-jacent" in n for n in md.notes)
    assert md.iv_history is None
    assert prov.fallback_ticker("SPX") == "^SPX"


def test_ibkr_unavailable_when_port_closed():
    ok, why = IBKRProvider.available(host="127.0.0.1", port=1)
    assert not ok and "injoignable" in why


# ---------------------------------------------------------------------------
# Bloomberg (requêtes simulées)
# ---------------------------------------------------------------------------

def test_bloomberg_ticker_helpers():
    assert normalize_ticker("aapl") == "AAPL US Equity"
    assert normalize_ticker("spx index") == "SPX Index"
    assert normalize_ticker("MC FP") == "MC FP Equity"
    assert parse_option_ticker("AAPL US 12/19/25 C200 Equity") == (dt.date(2025, 12, 19), "C", 200.0)
    assert parse_option_ticker("SPXW US 01/16/2026 P5800.5 Index") == (dt.date(2026, 1, 16), "P", 5800.5)
    p = BloombergProvider()
    assert p.fallback_ticker("MC FP Equity") == "MC.PA"
    assert p.fallback_ticker("SX5E Index") == "^STOXX50E"
    assert p.fallback_ticker("AAPL") == "AAPL"


class FakeBloomberg(BloombergProvider):
    def connect(self):
        pass

    def close(self):
        pass

    def _ref(self, securities, fields, overrides=None):
        out = {}
        for s in securities:
            if s == "AAPL US Equity":
                out[s] = {"PX_LAST": 200.0, "CRNCY": "USD", "EQY_DVD_YLD_IND": 0.5,
                          "EXPECTED_REPORT_DT": pd.Timestamp(TODAY + dt.timedelta(days=20))}
            elif s == "USGG3M Index":
                out[s] = {"PX_LAST": 4.2}
            else:
                exp, right, k = parse_option_ticker(s)
                px = float(bs_price(200, k, 45 / 365, 0.04, 0.005, 0.28, right == "C"))
                out[s] = {"PX_BID": px * 0.98, "PX_ASK": px * 1.02, "PX_LAST": px, "VOLUME": 5,
                          "OPEN_INT": 100, "IVOL_MID": 28.0, "OPT_PUT_CALL": "Call" if right == "C" else "Put",
                          "OPT_EXER_TYP": "American"}
        return out

    def _bulk(self, security, field, overrides=None):
        e1 = TODAY + dt.timedelta(days=45)
        e2 = TODAY + dt.timedelta(days=400)
        rows = []
        for e in (e1, e2):
            for k in range(150, 251, 5):
                for r in "CP":
                    rows.append({"Security Description": f"AAPL US {e:%m/%d/%y} {r}{k} Equity"})
        return rows

    def _hist(self, security, fields, start, end):
        h = simulate_history(n_days=300, spot_end=200)
        if fields == ["30DAY_IMPVOL_100.0%MNY_DF"]:
            return pd.DataFrame({"date": h.index, fields[0]: 28.0})
        return pd.DataFrame({"date": h.index, "PX_OPEN": h["open"], "PX_HIGH": h["high"],
                             "PX_LOW": h["low"], "PX_LAST": h["close"]})


def test_bloomberg_load_with_fake_session():
    md = FakeBloomberg().load("AAPL", dte_min=7, dte_max=90)
    assert md.spot == 200.0 and md.currency == "USD"
    assert set(md.chain["expiry"]) == {TODAY + dt.timedelta(days=45)}
    assert np.allclose(md.chain["vendor_iv"], 0.28)
    assert set(md.chain["style"]) == {"american"}
    assert md.rate == pytest.approx(np.log1p(0.042))
    assert md.div_yield == pytest.approx(np.log1p(0.005))
    assert md.earnings_date == TODAY + dt.timedelta(days=20)
    assert md.iv_history.iloc[-1] == pytest.approx(0.28)
    assert len(md.history) == 300


# ---------------------------------------------------------------------------
# Yahoo (faux objet yfinance)
# ---------------------------------------------------------------------------

class FakeYTicker:
    def __init__(self, sym, closed_market=False):
        self.sym = sym
        self.closed = closed_market
        self.e = (TODAY + dt.timedelta(days=30)).isoformat()
        self.fast_info = {"last_price": 100.0}

    @property
    def options(self):
        return (self.e, (TODAY + dt.timedelta(days=500)).isoformat())

    def option_chain(self, e):
        k = np.arange(60, 141, 5.0)
        def side(call):
            px = bs_price(100, k, 30 / 365, 0.04, 0.0, 0.3, call)
            bid = np.zeros_like(px) if self.closed else px * 0.98
            ask = np.zeros_like(px) if self.closed else px * 1.02
            return pd.DataFrame({"strike": k, "bid": bid, "ask": ask, "lastPrice": px,
                                 "volume": 10, "openInterest": 100})
        return SimpleNamespace(calls=side(True), puts=side(False))

    def history(self, period="2y", auto_adjust=False):
        h = simulate_history(n_days=300)
        if self.sym == "^IRX":
            h["close"] = 4.5
        return h.rename(columns=str.capitalize)

    @property
    def dividends(self):
        idx = pd.DatetimeIndex([pd.Timestamp(TODAY) - pd.Timedelta(days=d) for d in (30, 120, 210, 300)],
                               tz="America/New_York")
        return pd.Series([0.25] * 4, index=idx)

    @property
    def calendar(self):
        return {"Earnings Date": [TODAY + dt.timedelta(days=12)]}


def test_yahoo_provider_with_fake_yfinance(monkeypatch):
    from option_screener.data.yahoo import YahooProvider
    prov = YahooProvider()
    monkeypatch.setattr(prov, "_t", lambda s: FakeYTicker(s))
    md = prov.load("AAPL", dte_min=7, dte_max=90, moneyness=0.25)
    assert set(md.chain["expiry"]) == {TODAY + dt.timedelta(days=30)}
    assert md.chain["strike"].between(75, 125).all()
    assert md.rate == pytest.approx(np.log1p(0.045))
    assert md.div_yield == pytest.approx(0.01, abs=1e-3)
    assert md.earnings_date == TODAY + dt.timedelta(days=12)
    assert md.style == "american" and len(md.history) == 300


def test_yahoo_closed_market_note(monkeypatch):
    from option_screener.data.yahoo import YahooProvider
    prov = YahooProvider()
    monkeypatch.setattr(prov, "_t", lambda s: FakeYTicker(s, closed_market=True))
    md = prov.load("AAPL", dte_min=7, dte_max=90)
    assert md.chain["bid"].isna().all()
    assert any("marché fermé" in n for n in md.notes)
    prov2 = YahooProvider(use_last_if_no_quote=True)
    monkeypatch.setattr(prov2, "_t", lambda s: FakeYTicker(s, closed_market=True))
    md2 = prov2.load("AAPL", dte_min=7, dte_max=90)
    assert (md2.chain["bid"] == md2.chain["last"]).all()
