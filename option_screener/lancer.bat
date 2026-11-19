@echo off
rem Lance le screener de vol. Au premier lancement : crée l'environnement Python
rem (dossier .venv) et installe les dépendances. Ensuite : démarrage direct.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto venv_ok
echo Premiere installation : creation de l'environnement Python...
py -3 -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if exist ".venv\Scripts\python.exe" goto venv_ok
echo.
echo Python 3.10 ou plus recent est introuvable.
echo Installe-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH",
echo puis relance ce fichier.
pause
exit /b 1

:venv_ok
rem Installe ou met a jour les dependances seulement si requirements.txt a change
fc /b requirements.txt .venv\requirements-installees.txt >nul 2>&1
if not errorlevel 1 goto deps_ok
echo Installation des dependances, patiente une minute...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo Echec de l'installation des dependances.
  pause
  exit /b 1
)
copy /y requirements.txt .venv\requirements-installees.txt >nul

:deps_ok
if not exist "config.toml" copy /y config.example.toml config.toml >nul
".venv\Scripts\python.exe" -m streamlit run app.py --browser.gatherUsageStats false
pause
