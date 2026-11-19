#!/usr/bin/env bash
# Lance le screener de vol (Mac / Linux). Premier lancement : crée .venv et installe.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Première installation : création de l'environnement Python..."
  python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/requirements-installees.txt; then
  echo "Installation des dépendances..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  cp requirements.txt .venv/requirements-installees.txt
fi
[ -f config.toml ] || cp config.example.toml config.toml
exec .venv/bin/python -m streamlit run app.py --browser.gatherUsageStats false
