"""Fabrique des sources de données et détection de celles disponibles."""

from __future__ import annotations

from .data.base import DataProvider

SOURCES = {
    "demo": "Démo (données simulées)",
    "yahoo": "Yahoo Finance (gratuit, différé)",
    "ibkr": "Interactive Brokers (TWS / Gateway)",
    "bloomberg": "Bloomberg (Terminal)",
    "csv": "Fichier CSV",
}


def detect_sources(cfg: dict) -> dict[str, tuple[bool, str]]:
    """{source: (disponible, raison si indisponible)}."""
    from .data.bloomberg import BloombergProvider
    from .data.ibkr import IBKRProvider
    from .data.yahoo import YahooProvider

    ib = cfg.get("ibkr", {})
    bb = cfg.get("bloomberg", {})
    return {
        "demo": (True, ""),
        "yahoo": YahooProvider.available(),
        "ibkr": IBKRProvider.available(host=ib.get("host", "127.0.0.1"), port=ib.get("port", 7497)),
        "bloomberg": BloombergProvider.available(host=bb.get("host", "localhost"),
                                                 port=bb.get("port", 8194)),
        "csv": (True, ""),
    }


def make_provider(source: str, cfg: dict, *, csv_file=None, csv_spot=None, csv_history=None,
                  csv_style="american", csv_dayfirst=False, yahoo_use_last=False,
                  ibkr_delayed=None) -> DataProvider:
    from .data.yahoo import YahooProvider

    yahoo_ok, _ = YahooProvider.available()
    fallback = YahooProvider() if yahoo_ok else None
    if source == "demo":
        from .data.demo import DemoProvider
        return DemoProvider()
    if source == "yahoo":
        return YahooProvider(use_last_if_no_quote=yahoo_use_last)
    if source == "ibkr":
        from .data.ibkr import IBKRProvider
        ib = cfg.get("ibkr", {})
        return IBKRProvider(
            host=ib.get("host", "127.0.0.1"), port=ib.get("port", 7497),
            client_id=ib.get("client_id", 17),
            delayed=ib.get("donnees_differees", False) if ibkr_delayed is None else ibkr_delayed,
            max_expiries=ib.get("max_echeances", 12),
            max_strikes_per_expiry=ib.get("max_strikes_par_echeance", 40),
            fallback=fallback)
    if source == "bloomberg":
        from .data.bloomberg import BloombergProvider
        bb = cfg.get("bloomberg", {})
        return BloombergProvider(host=bb.get("host", "localhost"), port=bb.get("port", 8194),
                                 fallback=fallback)
    if source == "csv":
        from .data.csv_loader import CSVProvider
        if csv_file is None:
            raise ValueError("Aucun fichier CSV fourni")
        return CSVProvider(csv_file, spot=csv_spot, history_file=csv_history, style=csv_style,
                           dayfirst=csv_dayfirst, fallback=fallback)
    raise ValueError(f"Source inconnue : {source}")
