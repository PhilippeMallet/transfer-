"""Chargement de la configuration locale (config.toml, sinon l'exemple)."""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.toml"
EXAMPLE_PATH = HERE / "config.example.toml"


def _read(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def load_config() -> dict:
    """Fusionne l'exemple (valeurs par défaut) et le config.toml local."""
    cfg = _read(EXAMPLE_PATH)
    local = _read(CONFIG_PATH)
    for section, values in local.items():
        if isinstance(values, dict):
            cfg.setdefault(section, {}).update(values)
        else:
            cfg[section] = values
    return cfg
