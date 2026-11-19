# Screener de vol implicite

Tu choisis le sens (calls ou puts, horizon). L'outil répond à une question : **à ce prix-là, est-ce que je paie la vol trop cher ?**

Pour chaque option, il compare la **vol implicite de l'ask**, c'est-à-dire ce que tu paies en achetant, à une **vol « juste »** estimée sur l'historique du sous-jacent. Il traduit l'écart en points de vol et en dollars, puis donne un verdict : **Bon marché**, **Neutre** ou **Cher**.

Sources de données :
- **Interactive Brokers** : TWS ou IB Gateway doit tourner sur le PC.
- **Bloomberg** : uniquement sur le PC du Terminal.
- **Yahoo Finance** : gratuit, différé, fonctionne partout.
- **Fichier CSV**.
- **Démo** : données simulées, pour découvrir l'outil.

---

## 1. Installation sur un PC

1. Installe **Python 3.10 ou plus récent** depuis [python.org](https://www.python.org/downloads/). Sous Windows, coche **« Add python.exe to PATH »**.
2. Récupère le code :
   ```bash
   git clone https://github.com/philippemallet/transfer-.git
   ```
3. Lance l'outil :
   - **Windows** : double-clic sur `option_screener\lancer.bat`.
   - **Mac / Linux** : `option_screener/lancer.sh`.

Au premier lancement, le script prépare tout (environnement Python dans `option_screener/.venv`, dépendances, fichier `config.toml`). Compte 1 à 2 minutes. Ensuite, l'appli s'ouvre directement dans ton navigateur (adresse `http://localhost:8501`).

### Sur plusieurs PC
- Fais le `git clone` sur chaque PC, une seule fois.
- **Mise à jour** : `git pull` dans le dossier du repo, puis relance `lancer.bat`. Les nouvelles dépendances s'installent automatiquement.
- Chaque PC a son propre `option_screener/config.toml` : port IBKR, source par défaut, seuils. Ce fichier n'est pas envoyé sur GitHub, donc les réglages d'un PC ne gênent pas les autres.
- L'appli détecte au démarrage les sources disponibles sur le PC et affiche celles qui manquent, avec la raison.

---

## 2. Sources de données

### Yahoo Finance (aucune configuration)
- Gratuit, différé d'environ 15 min. Couvre bien les actions et ETF US, ainsi que `^SPX`, `^NDX` et `^RUT`.
- Hors séance, Yahoo renvoie souvent bid = ask = 0. Coche alors « Hors séance : utiliser le dernier prix », mais ce prix peut être ancien.
- Pas d'historique de vol implicite : l'IV est placée dans le **cône de vol réalisée** au lieu d'un IV rank.
- Yahoo sert aussi de **secours automatique** aux autres sources pour l'historique, le taux, les dividendes et la date de résultats.

### Interactive Brokers
1. Lance **TWS** ou **IB Gateway** et connecte-toi.
2. Dans *Configuration → API → Settings* :
   - coche **Enable ActiveX and Socket Clients** ;
   - note le **port** : 7497 en TWS papier, 7496 en TWS réel, 4002 et 4001 pour Gateway ;
   - « Read-Only API » peut rester coché : l'outil ne passe **aucun ordre**.
3. Reporte le port dans `option_screener/config.toml` (section `[ibkr]`).

**Coût** :
- L'API est gratuite.
- Les cotations d'options US en temps réel demandent l'abonnement **OPRA Top of Book**. Il coûte 1,50 $/mois en non-professionnel et il est offert si tu génères plus de 20 $ de commissions dans le mois (tarifs IBKR fin 2026, à vérifier dans *Account Settings → Market Data Subscriptions*).
- Sans abonnement, coche « Données différées » (≈ 15 min de retard, gratuit). Ça suffit pour juger la vol.
- L'historique quotidien du sous-jacent peut demander l'abonnement actions correspondant. S'il est refusé, l'outil le prend sur Yahoo et l'indique.

**Bon à savoir** :
- Une seule session TWS/Gateway par identifiant à la fois. Si tu ouvres TWS sur un 2ᵉ PC, le 1ᵉʳ est déconnecté.
- IBKR limite à ~100 cotations simultanées. L'outil cote donc par lots de 90 et se limite à 12 échéances × 40 strikes autour du spot (réglable dans `config.toml`). Un chargement prend de quelques secondes à environ une minute.
- IBKR fournit l'**historique de la vol implicite** : tu as un vrai **IV rank / IV percentile sur 1 an**.
- Symboles acceptés :
  - actions et ETF : `AAPL`, `SPY` ;
  - indices : `SPX`, `NDX`, `RUT`, `ESTX50`, `DAX`, `CAC40` ;
  - forme explicite `SYMBOLE:BOURSE:DEVISE`, par exemple `ASML:AEB:EUR`.

### Bloomberg (PC du Terminal uniquement)
1. Sur ce PC, installe le paquet Bloomberg, une seule fois, dans l'environnement de l'outil :
   ```bash
   option_screener\.venv\Scripts\python.exe -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
   ```
2. Le Terminal doit être ouvert et connecté (l'API écoute sur `localhost:8194`).
3. Tickers au format Bloomberg : `AAPL US Equity`, `SPX Index`, `SX5E Index`, `MC FP Equity`. Sans clé jaune, `US Equity` est ajouté.

Champs utilisés (liste `FIELDS` dans `data/bloomberg.py`) :
- `OPT_CHAIN`, `PX_BID`, `PX_ASK`, `IVOL_MID`, `OPEN_INT`, `OPT_EXER_TYP` ;
- `30DAY_IMPVOL_100.0%MNY_DF` (historique d'IV → IV rank) ;
- `EXPECTED_REPORT_DT` (date de résultats), `EQY_DVD_YLD_IND` (dividende), `USGG3M Index` (taux).

Si une donnée manque, vérifie le nom du champ sur ton Terminal avec `FLDS`.

Licence : les données restent en mémoire, rien n'est enregistré sur disque ni envoyé ailleurs. Vérifie quand même les conditions de ta licence (usage personnel, plafonds quotidiens de l'API Desktop).

### Fichier CSV
Pour un marché non couvert, ou un export de ton broker ou d'Excel. Colonnes reconnues, en français ou en anglais (casse et accents ignorés) :

| Donnée | Noms acceptés (exemples) |
|---|---|
| Échéance | `expiry`, `expiration`, `Échéance`, `maturity` |
| Strike | `strike`, `Prix d'exercice` |
| Type | `type`, `right`, `Call/Put` (valeurs C/P/Call/Put) |
| Bid, Ask | `bid`, `ask`, `Achat`, `Vente` (**obligatoires**) |
| Facultatif | `last`, `volume`, `open_interest` / `OI`, `spot` / `Sous-jacent`, `style` |

- Un symbole OCC (`AAPL251219C00200000`) dans une colonne `contract` ou `symbol` suffit à la place d'échéance / strike / type.
- Le séparateur (`,` `;` tabulation) et la virgule décimale sont détectés automatiquement.
- Sans historique, l'outil tente Yahoo avec le ticker saisi. Sinon, choisis le modèle de vol juste « Manuelle ».

---

## 3. Lire les résultats

| Colonne | Sens |
|---|---|
| **IV ask** | Vol implicite du prix ask (celui que tu paies). Calculée par arbre binomial pour l'exercice américain, par Black-Scholes pour l'européen. |
| **Vol juste** | Vol que le sous-jacent devrait réaliser d'ici l'échéance (modèle au choix, voir plus bas). |
| **Écart (pts)** | IV ask − vol juste. C'est la base du verdict. |
| **Edge $** | (ask − prix à vol juste) × multiplicateur. Négatif : l'option coûte moins que sa valeur. |
| **Spread (pts)** | Demi-fourchette bid/ask exprimée en points de vol. |
| **Skew (pts)** | IV du strike − IV ATM de la même échéance. |
| **IV/RV** | IV mid / vol réalisée sur la même durée. |
| **Point mort** | Niveau du sous-jacent à l'échéance pour récupérer la prime. |
| **Alertes** | Résultats avant l'échéance, spread large, « cher surtout à cause du spread : vise le mid », écart avec l'IV du fournisseur. |

**Verdict** :
- *Cher* si l'écart dépasse **+3 pts**.
- *Bon marché* s'il est sous **−1 pt**.
- *Neutre* entre les deux.

Les deux seuils sont réglables. Le seuil « Cher » est positif parce que les vendeurs d'options exigent en moyenne une petite prime au-dessus de la vol réalisée (prime de risque de variance) : un écart de +1 pt est normal.

**Modèles de vol juste** :
- **GARCH(1,1)** (défaut) : part de la vol récente et la fait revenir vers sa moyenne de long terme. La vol juste est donc différente pour chaque échéance.
- **EWMA 0,94** : vol récente, plate sur toutes les échéances.
- **Yang-Zhang** : vol réalisée sur une fenêtre égale à la durée de vie de l'option. Elle prend en compte les gaps d'ouverture, contrairement à Parkinson et Garman-Klass.
- **Manuelle** : tu fixes la valeur.

**Résultats d'entreprise** :
- Une publication avant l'échéance gonfle l'IV : l'option est signalée.
- L'outil estime le **mouvement que le marché price** pour la publication, à partir des échéances qui l'encadrent.
- Si tu as ta propre estimation, saisis-la dans « Mouvement attendu aux résultats » : elle est ajoutée à la vol juste des échéances concernées.

**IV rank et cône de vol** :
- Avec IBKR ou Bloomberg, l'**IV rank** situe l'IV actuelle entre son plus bas et son plus haut sur 1 an.
- Sans historique d'IV, le **cône de vol** situe l'IV dans la distribution historique de la vol réalisée au même horizon.

**Onglets** : structure par terme (IV ATM vs vol juste), smile d'une échéance (fourchette bid/ask en vol), cône de vol.

---

## 4. Ligne de commande

Depuis la racine du repo, avec l'environnement de l'outil :

```bash
option_screener/.venv/bin/python -m option_screener SPY --type call --dte 20-60
```
```bash
option_screener/.venv/bin/python -m option_screener AAPL --source ibkr --type put --delta 0.2-0.5 --move-resultats 0.06
```
```bash
option_screener/.venv/bin/python -m option_screener DEMO --source demo --export resultat.csv
```

Sous Windows, remplace `option_screener/.venv/bin/python` par `option_screener\.venv\Scripts\python.exe`. `--help` liste toutes les options.

---

## 5. Hypothèses et limites

- **Prix utilisés** : bid, ask et mid. Le verdict se base sur l'ask (achat). Si tu vends, regarde plutôt l'IV bid.
- **Exercice américain** : arbre binomial Cox-Ross-Rubinstein (150 pas, corrigé par variable de contrôle).
- **Grecques** : celles de Black-Scholes à l'IV mid. Pour l'exercice américain, c'est une approximation suffisante pour un screener.
- **Dividendes** :
  - européen (indices) : le taux de dividende implicite est tiré de la parité call-put, échéance par échéance ;
  - américain : dividende des 12 derniers mois.

  Un titre difficile à emprunter peut donc montrer un écart entre IV des calls et IV des puts. La colonne « q parité » de l'onglet structure par terme le révèle.
- **La vol juste est une estimation statistique**, pas une garantie. Un événement que l'historique ne contient pas (OPA, procès, macro) n'y est pas.
- **Options très dans la monnaie** (|delta| > 0,9) : leur IV est mal déterminée car presque toute la prime est intrinsèque. Le filtre delta par défaut les écarte.

---

## 6. Développement

```bash
option_screener/.venv/bin/python -m pytest option_screener/tests
```

| Fichier | Rôle |
|---|---|
| `pricing.py` | Black-Scholes, binomial, vol implicite, forward implicite |
| `volatility.py` | Vols réalisées, EWMA, GARCH, cône, IV rank |
| `screener.py` | Calculs par option, verdicts, structure par terme |
| `data/` | Sources : `ibkr.py`, `bloomberg.py`, `yahoo.py`, `csv_loader.py`, `demo.py` (interface commune dans `base.py`) |
| `app.py`, `charts.py` | Appli Streamlit et graphiques |
| `__main__.py` | Ligne de commande |
