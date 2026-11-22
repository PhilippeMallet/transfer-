"""Source Bloomberg via l'API Desktop (``blpapi``).

Ne fonctionne que sur le PC où le Terminal Bloomberg est ouvert et connecté
(l'API écoute sur localhost:8194). Installation du paquet :

    pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi

Tickers au format Bloomberg : ``AAPL US Equity``, ``SPX Index``,
``SX5E Index``, ``MC FP Equity``. Sans clé jaune, ``US Equity`` est ajouté.

Les noms de champs utilisés sont regroupés dans ``FIELDS`` : vérifie-les sur
ton terminal avec FLDS si une donnée manque. Les données restent en mémoire,
rien n'est écrit sur disque.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import socket

import numpy as np
import pandas as pd

from .base import DataProvider, ProviderError, standardize_history

FIELDS = {
    "spot": "PX_LAST",
    "ohlc": ["PX_OPEN", "PX_HIGH", "PX_LOW", "PX_LAST"],
    "chain": "OPT_CHAIN",
    "option": ["PX_BID", "PX_ASK", "PX_LAST", "VOLUME", "OPEN_INT", "IVOL_MID",
               "OPT_STRIKE_PX", "OPT_EXPIRE_DT", "OPT_PUT_CALL", "OPT_EXER_TYP",
               "OPT_CONT_SIZE"],
    "iv_history": "30DAY_IMPVOL_100.0%MNY_DF",
    "earnings": "EXPECTED_REPORT_DT",
    "div_yield": "EQY_DVD_YLD_IND",
    "currency": "CRNCY",
}
RATE_TICKERS = {"USD": "USGG3M Index", "EUR": "EUR003M Index"}

YELLOW_KEYS = ("EQUITY", "INDEX", "COMDTY", "CURNCY", "GOVT", "CORP")
INDEX_TO_YAHOO = {"SPX": "^SPX", "NDX": "^NDX", "RTY": "^RUT", "INDU": "^DJI", "SX5E": "^STOXX50E",
                  "DAX": "^GDAXI", "CAC": "^FCHI", "UKX": "^FTSE", "SMI": "^SSMI", "VIX": "^VIX"}
EXCH_TO_YAHOO = {"FP": ".PA", "GY": ".DE", "GR": ".DE", "NA": ".AS", "LN": ".L", "IM": ".MI",
                 "SM": ".MC", "SW": ".SW", "BB": ".BR", "US": "", "UN": "", "UW": ""}
OPTION_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s+([CP])\s*([\d.]+)")


def normalize_ticker(ticker: str) -> str:
    t = " ".join(ticker.strip().split())
    if t.split(" ")[-1].upper() not in YELLOW_KEYS:
        t = f"{t} US Equity" if len(t.split(" ")) == 1 else f"{t} Equity"
    parts = t.split(" ")
    return " ".join(parts[:-1]).upper() + " " + parts[-1].capitalize()


def parse_option_ticker(desc: str):
    """'AAPL US 12/19/25 C200 Equity' -> (date(2025,12,19), 'C', 200.0)."""
    m = OPTION_RE.search(desc)
    if not m:
        return None
    d, right, strike = m.groups()
    fmt = "%m/%d/%y" if len(d.split("/")[-1]) == 2 else "%m/%d/%Y"
    return dt.datetime.strptime(d, fmt).date(), right, float(strike)


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) else float("nan")


def _coalesce(x, default: float) -> float:
    v = _f(x)
    return v if np.isfinite(v) and v > 0 else float(default)


class BloombergProvider(DataProvider):
    name = "Bloomberg"

    def __init__(self, host: str = "localhost", port: int = 8194, max_expiries: int = 12,
                 max_strikes_per_expiry: int = 40, fallback: DataProvider | None = None):
        super().__init__(fallback=fallback)
        self.host, self.port = host, int(port)
        self.max_expiries = max_expiries
        self.max_strikes = max_strikes_per_expiry
        self.session = None
        self._service = None

    @classmethod
    def available(cls, host: str = "localhost", port: int = 8194, **_) -> tuple[bool, str]:
        try:
            import blpapi  # noqa: F401
        except ImportError:
            return False, "paquet blpapi non installé (voir README)"
        try:
            with socket.create_connection((host, int(port)), timeout=0.5):
                return True, ""
        except OSError:
            return False, "Terminal Bloomberg non joignable (localhost:8194)"

    def fallback_ticker(self, ticker: str) -> str:
        parts = normalize_ticker(ticker).split(" ")
        sym, key = parts[0], parts[-1].upper()
        if key == "INDEX":
            return INDEX_TO_YAHOO.get(sym, "^" + sym)
        exch = parts[1] if len(parts) > 2 else "US"
        return sym.replace("/", "-") + EXCH_TO_YAHOO.get(exch, "")

    # --- session -----------------------------------------------------------
    def connect(self):
        if self.session is not None:
            return
        import blpapi
        opts = blpapi.SessionOptions()
        opts.setServerHost(self.host)
        opts.setServerPort(self.port)
        session = blpapi.Session(opts)
        if not session.start():
            raise ProviderError("Impossible de démarrer la session Bloomberg (Terminal ouvert ?)")
        if not session.openService("//blp/refdata"):
            session.stop()
            raise ProviderError("Service //blp/refdata indisponible")
        self.session = session
        self._service = session.getService("//blp/refdata")

    def close(self):
        if self.session is not None:
            try:
                self.session.stop()
            except Exception:  # noqa: BLE001
                pass
            self.session = None

    def _send(self, request):
        import blpapi
        self.session.sendRequest(request)
        msgs = []
        while True:
            ev = self.session.nextEvent(30000)
            if ev.eventType() == blpapi.Event.TIMEOUT:
                raise ProviderError("Bloomberg : délai dépassé")
            for msg in ev:
                if msg.hasElement("responseError"):
                    raise ProviderError(f"Bloomberg : {msg.getElement('responseError')}")
                msgs.append(msg)
            if ev.eventType() == blpapi.Event.RESPONSE:
                return msgs

    # --- trois requêtes élémentaires (surchargées dans les tests) ----------
    def _ref(self, securities, fields, overrides=None) -> dict:
        """ReferenceDataRequest -> {titre: {champ: valeur}}."""
        out: dict = {}
        for i in range(0, len(securities), 100):
            req = self._service.createRequest("ReferenceDataRequest")
            for s in securities[i:i + 100]:
                req.append("securities", s)
            for f in fields:
                req.append("fields", f)
            for k, v in (overrides or {}).items():
                o = req.getElement("overrides").appendElement()
                o.setElement("fieldId", k)
                o.setElement("value", v)
            for msg in self._send(req):
                if not msg.hasElement("securityData"):
                    continue
                arr = msg.getElement("securityData")
                for j in range(arr.numValues()):
                    sd = arr.getValueAsElement(j)
                    sec = sd.getElementAsString("security")
                    fd = sd.getElement("fieldData")
                    row = {}
                    for f in fields:
                        if fd.hasElement(f):
                            el = fd.getElement(f)
                            row[f] = el if el.isArray() else el.getValue()
                    out[sec] = row
        return out

    def _bulk(self, security, field, overrides=None) -> list[dict]:
        """Champ « bulk » (ex. OPT_CHAIN) -> liste de dicts."""
        el = self._ref([security], [field], overrides).get(security, {}).get(field)
        if el is None:
            return []
        rows = []
        for i in range(el.numValues()):
            item = el.getValueAsElement(i)
            rows.append({str(item.getElement(k).name()): item.getElement(k).getValue()
                         for k in range(item.numElements())})
        return rows

    def _hist(self, security, fields, start: dt.date, end: dt.date) -> pd.DataFrame:
        req = self._service.createRequest("HistoricalDataRequest")
        req.append("securities", security)
        for f in fields:
            req.append("fields", f)
        req.set("startDate", start.strftime("%Y%m%d"))
        req.set("endDate", end.strftime("%Y%m%d"))
        req.set("periodicitySelection", "DAILY")
        rows = []
        for msg in self._send(req):
            if not msg.hasElement("securityData"):
                continue
            fd = msg.getElement("securityData").getElement("fieldData")
            for i in range(fd.numValues()):
                p = fd.getValueAsElement(i)
                row = {"date": p.getElementAsDatetime("date")}
                for f in fields:
                    row[f] = p.getElementAsFloat(f) if p.hasElement(f) else np.nan
                rows.append(row)
        return pd.DataFrame(rows)

    # --- données -----------------------------------------------------------
    def spot(self, ticker: str) -> float:
        sec = normalize_ticker(ticker)
        data = self._ref([sec], [FIELDS["spot"], FIELDS["currency"]]).get(sec, {})
        self.currency = str(data.get(FIELDS["currency"], "USD") or "USD").upper()
        if self.currency in ("EUR", "CHF", "GBP", "GBp"):
            self.expiry_tz = "Europe/Paris"
        self.default_style = "european" if sec.upper().endswith("INDEX") else "american"
        return _f(data.get(FIELDS["spot"]))

    def history(self, ticker: str, years: int = 2) -> pd.DataFrame:
        end = dt.date.today()
        df = self._hist(normalize_ticker(ticker), FIELDS["ohlc"], end - dt.timedelta(days=365 * years + 5), end)
        if df.empty:
            raise ProviderError("historique vide")
        df = df.rename(columns=dict(zip(FIELDS["ohlc"], ["open", "high", "low", "close"])))
        return standardize_history(df)

    def iv_history(self, ticker: str) -> pd.Series:
        end = dt.date.today()
        df = self._hist(normalize_ticker(ticker), [FIELDS["iv_history"]], end - dt.timedelta(days=370), end)
        if df.empty:
            raise ProviderError("historique d'IV vide")
        s = df.set_index(pd.to_datetime(df["date"]))[FIELDS["iv_history"]] / 100.0
        return s.dropna()

    def rate(self) -> float:
        sec = RATE_TICKERS.get(self.currency)
        if sec is None:
            raise NotImplementedError
        v = _f(self._ref([sec], ["PX_LAST"]).get(sec, {}).get("PX_LAST"))
        if not np.isfinite(v):
            raise ProviderError(f"taux {sec} indisponible")
        return float(np.log1p(v / 100.0))

    def dividend_yield(self, ticker: str, spot: float) -> float:
        sec = normalize_ticker(ticker)
        v = _f(self._ref([sec], [FIELDS["div_yield"]]).get(sec, {}).get(FIELDS["div_yield"]))
        return float(np.log1p(v / 100.0)) if np.isfinite(v) else 0.0

    def earnings_date(self, ticker: str):
        sec = normalize_ticker(ticker)
        if sec.upper().endswith("INDEX"):
            return None
        v = self._ref([sec], [FIELDS["earnings"]]).get(sec, {}).get(FIELDS["earnings"])
        if v is None:
            return None
        return pd.Timestamp(v).date()

    def chain(self, ticker: str, spot: float, dte_min: int, dte_max: int,
              moneyness: float, today: dt.date | None = None) -> pd.DataFrame:
        sec = normalize_ticker(ticker)
        today = today or dt.date.today()
        listing = self._bulk(sec, FIELDS["chain"])
        parsed = []
        for row in listing:
            desc = str(next(iter(row.values()))) if row else ""
            p = parse_option_ticker(desc)
            if p is None:
                continue
            exp, right, strike = p
            days = (exp - today).days
            if dte_min <= days <= dte_max:
                parsed.append((desc, exp, right, strike))
        if not parsed:
            return pd.DataFrame()
        selected = self._select(parsed, spot, moneyness, today)
        data = self._ref([p[0] for p in selected], FIELDS["option"])
        rows = []
        for desc, exp, right, strike in selected:
            d = data.get(desc, {})
            style = str(d.get("OPT_EXER_TYP", "")).lower()
            iv = _f(d.get("IVOL_MID"))
            rows.append({
                "expiry": exp, "strike": _coalesce(d.get("OPT_STRIKE_PX"), strike),
                "type": str(d.get("OPT_PUT_CALL", right))[:1].upper() or right,
                "bid": _f(d.get("PX_BID")), "ask": _f(d.get("PX_ASK")),
                "last": _f(d.get("PX_LAST")), "volume": _f(d.get("VOLUME")),
                "open_interest": _f(d.get("OPEN_INT")),
                "vendor_iv": iv / 100.0 if np.isfinite(iv) else np.nan,
                "style": "european" if style.startswith("euro") else
                         "american" if style.startswith("amer") else self.default_style,
                "multiplier": _coalesce(d.get("OPT_CONT_SIZE"), 100.0),
            })
        df = pd.DataFrame(rows)
        return df[df["bid"].notna() | df["ask"].notna()].reset_index(drop=True)

    def _select(self, parsed, spot, moneyness, today):
        by_exp: dict = {}
        for item in parsed:
            by_exp.setdefault(item[1], []).append(item)
        expiries = sorted(by_exp)
        if len(expiries) > self.max_expiries:
            idx = np.unique(np.round(np.linspace(0, len(expiries) - 1, self.max_expiries)).astype(int))
            expiries = [expiries[i] for i in idx]
        out = []
        for e in expiries:
            T = max((e - today).days, 1) / 365
            band = min(moneyness, max(0.05, 2.5 * 0.35 * math.sqrt(T)))
            items = [it for it in by_exp[e] if abs(it[3] / spot - 1) <= band]
            strikes = sorted({it[3] for it in items}, key=lambda k: abs(k - spot))[:self.max_strikes]
            out.extend(it for it in items if it[3] in set(strikes))
        return out
