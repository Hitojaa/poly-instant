# poly-instant - Guide Complet

## Polymarket Intelligence Engine - Analyse avancee, Smart Money Tracking & Copy Trading

---

## Table des matieres

1. [Vue d'ensemble](#vue-densemble)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Architecture du code](#architecture-du-code)
5. [Toutes les commandes](#toutes-les-commandes)
6. [Guide d'utilisation pas a pas](#guide-dutilisation-pas-a-pas)
7. [Comment ca marche en detail](#comment-ca-marche-en-detail)
8. [Combien de temps attendre avant de trader](#combien-de-temps-attendre)
9. [FAQ](#faq)

---

## Vue d'ensemble

poly-instant est un outil d'intelligence pour Polymarket qui combine :

- **Analyse de marche avancee** : Kelly Criterion, Expected Value, probabilites bayesiennes, momentum/RSI, detection de baleines, profil de volume
- **Wallet Tracker** : indexe les trades on-chain, score les wallets par win rate/PnL/timing, identifie les smart wallets
- **Smart Money Detection** : detecte les wallets qui achetent avant les bonnes resolutions, clusters sybil, activite anormale
- **Copy Trading** : surveille les smart wallets en temps reel et envoie des alertes Telegram quand ils tradent
- **Notifications Telegram** : alertes formatees pour chaque type de signal

### Ce que le programme fait concretement

1. Il scanne les marches Polymarket en continu
2. Il collecte les prix, orderbooks, et volumes dans une base SQLite
3. Il analyse chaque marche avec des mathematiques poussees (Kelly, EV, Bayes, RSI...)
4. Il indexe les trades des marches resolus pour identifier qui gagne
5. Il score chaque wallet selon son win rate, timing, volume, PnL
6. Il detecte les clusters de wallets suspects (sybil)
7. Il genere des signaux de trading prioritises
8. Il t'envoie des alertes Telegram en temps reel

---

## Installation

```bash
# 1. Cloner le repo
git clone <repo-url>
cd poly-instant

# 2. Installer les dependances
pip install -r requirements.txt

# 3. Copier et configurer le .env
cp .env.example .env
# Editer .env avec tes cles (voir section Configuration)

# 4. Tester que ca marche
python main.py
```

### Dependances

- `requests` : appels API
- `python-dotenv` : variables d'environnement
- `tabulate` : tableaux formattes en CLI
- `pandas` : manipulation de donnees
- `websocket-client` : connexion temps reel (futur)

Tout est en **Python pur**, pas besoin de Node.js ou autre.

---

## Configuration

### Fichier `.env`

```bash
# OBLIGATOIRE pour rien - la lecture de marche est gratuite
# OPTIONNEL pour les notifications Telegram
TELEGRAM_BOT_TOKEN=ton_token_ici
TELEGRAM_CHAT_ID=ton_chat_id_ici

# RECOMMANDE pour le wallet tracker (analyse on-chain)
POLYGONSCAN_API_KEY=ta_cle_ici

# Config
SCAN_INTERVAL=60        # Intervalle de scan en secondes
MIN_PROFIT_PCT=2.0      # Profit minimum pour les alertes d'arbitrage
```

### Setup Telegram (5 minutes)

1. Ouvre Telegram, cherche `@BotFather`
2. Envoie `/newbot`
3. Donne un nom a ton bot (ex: "PolyInstant Alerts")
4. BotFather te donne un token -> copie-le dans `TELEGRAM_BOT_TOKEN`
5. Envoie un message a ton bot (n'importe quoi)
6. Va sur `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
7. Cherche `"chat":{"id":XXXXXXX}` -> copie le nombre dans `TELEGRAM_CHAT_ID`

### Setup Polygonscan API (2 minutes)

1. Va sur https://polygonscan.com
2. Cree un compte (gratuit)
3. Va dans "API Keys" -> "Add"
4. Copie la cle dans `POLYGONSCAN_API_KEY`

Le free tier donne 5 calls/sec, c'est largement suffisant.

**Sans cle Polygonscan**, le wallet tracker fonctionne quand meme via l'API CLOB de Polymarket, mais avec moins de donnees historiques.

---

## Architecture du code

```
poly-instant/
├── main.py                      # CLI - 16 commandes
├── requirements.txt
├── .env.example
├── GUIDE.md                     # Ce fichier
├── data/
│   └── polymarket.db            # Base SQLite (creee automatiquement)
└── polyinstant/
    ├── __init__.py
    ├── client.py                # Client API Polymarket (Gamma + CLOB)
    ├── scanner.py               # Scanner d'arbitrage et mispricing
    ├── analyzer.py              # Analyse basique d'un marche
    ├── analytics.py             # MOTEUR MATHEMATIQUE
    │                            #   - Kelly Criterion
    │                            #   - Expected Value
    │                            #   - Bayesian probability updates
    │                            #   - Momentum / RSI / Volatilite
    │                            #   - Detection de baleines
    │                            #   - Volume profile (POC, Value Area)
    │                            #   - Score d'efficience du marche
    │                            #   - MarketPredictor (prediction directionnelle)
    ├── collector.py             # Collecteur de donnees SQLite
    │                            #   - Snapshots de prix en continu
    │                            #   - Historique d'orderbook
    │                            #   - Log d'evenements
    ├── signals.py               # Moteur de signaux composite
    │                            #   - Combine tous les indicateurs
    │                            #   - Scoring et prioritisation
    ├── notifier.py              # Alertes Telegram formatees
    │                            #   - Arbitrage, signaux, baleines
    │                            #   - Momentum, efficience, rapports
    ├── blockchain.py            # Client Polygon/Polygonscan
    │                            #   - Transactions on-chain
    │                            #   - Trades CLOB (maker/taker)
    │                            #   - Marches resolus
    │                            #   - Token transfers ERC-1155
    ├── wallet_tracker.py        # SYSTEME DE TRACKING DE WALLETS
    │                            #   - Indexation des trades
    │                            #   - Scoring multi-facteurs
    │                            #   - Leaderboard
    │                            #   - Profils detailles
    │                            #   - Watchlist
    ├── smart_money.py           # DETECTION SMART MONEY
    │                            #   - Activite pre-resolution
    │                            #   - Clusters sybil
    │                            #   - Activite anormale
    │                            #   - Analyse reseau de wallets
    │                            #   - Smart money flow par marche
    └── copy_trader.py           # COPY TRADING EN TEMPS REEL
                                 #   - Monitoring de la watchlist
                                 #   - Consensus smart money
                                 #   - Alertes de trades
                                 #   - Rapport complet
```

### Base de donnees SQLite

La DB est creee automatiquement dans `data/polymarket.db`. Tables :

| Table | Contenu |
|-------|---------|
| `price_snapshots` | Prix YES/NO toutes les X secondes |
| `orderbook_snapshots` | Best bid/ask, spread, depth |
| `events` | Evenements detectes (baleines, momentum...) |
| `market_meta` | Metadata des marches suivis |
| `wallets` | Profil de chaque wallet indexe |
| `wallet_trades` | Historique trade par trade |
| `wallet_scores` | Scores calcules (composite, insider...) |
| `tracked_wallets` | Watchlist de wallets a suivre |

---

## Toutes les commandes

### Scan & Decouverte

| Commande | Description |
|----------|-------------|
| `python main.py scan [min%]` | Scanner d'arbitrage en continu (defaut: 2%) |
| `python main.py mispriced` | Marches avec forte conviction (YES > 85%) |
| `python main.py markets [limit]` | Liste des marches actifs |
| `python main.py top [limit]` | Top marches par volume avec score d'efficience |

### Analyse Approfondie

| Commande | Description |
|----------|-------------|
| `python main.py analyze <slug>` | Analyse complete : Kelly, EV, Bayes, whales, momentum, smart money flow |
| `python main.py signals` | Genere les signaux de trading (one-shot) |

### Monitoring

| Commande | Description |
|----------|-------------|
| `python main.py monitor [sec]` | Mode continu : collecte + analyse + alertes Telegram (defaut: 60s) |
| `python main.py history <slug> [hours]` | Historique des prix d'un marche |
| `python main.py stats` | Statistiques completes de la DB |

### Wallet Tracker & Smart Money

| Commande | Description |
|----------|-------------|
| `python main.py index [limit]` | Indexe les wallets depuis les marches resolus |
| `python main.py leaderboard [n] [tri]` | Classement des meilleurs wallets |
| `python main.py wallet <address>` | Profil complet d'un wallet |
| `python main.py network <address>` | Analyse reseau (co-traders, sybil) |
| `python main.py watchlist` | Afficher la watchlist |
| `python main.py watchlist add <addr>` | Ajouter un wallet a la watchlist |
| `python main.py watchlist auto` | Ajouter auto les meilleurs wallets |

### Intelligence

| Commande | Description |
|----------|-------------|
| `python main.py smartmoney` | Scan d'intelligence complet |
| `python main.py smartmoney pre [min]` | Analyse pre-resolution (fenetre en minutes) |
| `python main.py smartmoney sybil` | Detection de clusters sybil |
| `python main.py smartmoney abnormal` | Activite anormale recente |
| `python main.py smartmoney flow <slug>` | Smart money flow sur un marche |
| `python main.py copytrade [sec]` | Copy-trading en temps reel |

---

## Guide d'utilisation pas a pas

### Etape 1 : Premier lancement (5 min)

```bash
# Voir les marches actifs
python main.py markets

# Voir les top marches par volume
python main.py top

# Scanner les arbitrages
python main.py scan
```

### Etape 2 : Analyser un marche (immediat)

```bash
# Copie le slug d'un marche depuis Polymarket (dans l'URL)
python main.py analyze will-trump-win-2028
```

Ca te donne :
- Prix et spread
- Probabilite bayesienne ajustee
- Kelly Criterion (combien miser)
- Expected Value
- Score d'efficience
- Volume profile avec supports/resistances
- Detection de baleines
- Smart money flow (si DB indexee)

### Etape 3 : Lancer le monitoring (laisser tourner)

```bash
# Scan toutes les 60 secondes (defaut)
python main.py monitor

# Scan toutes les 30 secondes (plus rapide)
python main.py monitor 30
```

**Laisse tourner en arriere-plan.** Plus ca tourne longtemps :
- Plus l'historique de prix est riche
- Plus les signaux de momentum sont precis
- Plus les alertes sont pertinentes

### Etape 4 : Construire la DB de wallets (10-30 min)

```bash
# Indexer 50 marches resolus recemment
python main.py index 50

# Indexer plus (plus de wallets, plus precis)
python main.py index 100
```

Ca va :
1. Recuperer les marches resolus sur Polymarket
2. Collecter tous les trades de ces marches
3. Identifier quel wallet a achete quoi et quand
4. Calculer win/loss et PnL pour chaque wallet
5. Scorer chaque wallet (composite, insider, timing...)
6. Marquer les "smart wallets" (score >= 70)

**Relance regulierement** pour enrichir la DB avec de nouveaux marches.

### Etape 5 : Explorer les wallets

```bash
# Voir le leaderboard
python main.py leaderboard

# Trier par win rate
python main.py leaderboard 30 win_rate

# Trier par insider score
python main.py leaderboard 30 insider_score

# Profil detaille d'un wallet
python main.py wallet 0x1234...

# Voir son reseau
python main.py network 0x1234...
```

### Etape 6 : Detection smart money

```bash
# Scan complet
python main.py smartmoney

# Qui a achete avant les resolutions ?
python main.py smartmoney pre

# Fenetre plus courte (30 min)
python main.py smartmoney pre 30

# Clusters sybil
python main.py smartmoney sybil

# Activite anormale
python main.py smartmoney abnormal

# Smart money sur un marche specifique
python main.py smartmoney flow will-trump-win-2028
```

### Etape 7 : Copy trading

```bash
# Ajouter auto les meilleurs wallets a la watchlist
python main.py watchlist auto

# Ou ajouter manuellement
python main.py watchlist add 0x1234... "Whale spotted"

# Voir la watchlist
python main.py watchlist

# Lancer le copy-trading
python main.py copytrade
```

---

## Comment ca marche en detail

### Mathematiques utilisees

#### Kelly Criterion
```
f* = (bp - q) / b
```
- `b` = odds - 1 (combien tu gagnes par dollar mise)
- `p` = probabilite estimee de gagner
- `q` = 1 - p
- `f*` = fraction optimale de ton bankroll a miser

On utilise le half-kelly (plus conservateur) par defaut.

#### Expected Value (EV)
```
EV = (prob_win * payout_win) + (prob_lose * payout_lose) - cost
```
Sur Polymarket : payout_win = 1.0 (moins 2% de frais), payout_lose = 0.
Un trade avec EV > 0 est profitable en esperance.

#### Bayesian Update
Met a jour la probabilite du marche en fonction de :
- Volume 24h (plus de volume = prix plus fiable)
- Liquidite (plus liquide = moins manipulable)
- Momentum du prix
- Desequilibre du carnet d'ordres

#### RSI (Relative Strength Index)
```
RSI = 100 - (100 / (1 + RS))
RS = moyenne des gains / moyenne des pertes
```
- RSI > 70 = surachete (potentiel de baisse)
- RSI < 30 = survendu (potentiel de hausse)

#### Sharpe Ratio (pour le scoring de wallets)
```
Sharpe = (rendement moyen) / (ecart-type des rendements)
```
Un Sharpe eleve = gains reguliers avec peu de variance = bon trader.

### Comment le wallet tracker detecte les insiders

1. **Indexation** : On collecte TOUS les trades des marches resolus
2. **Resolution mapping** : On sait quel outcome a gagne (YES ou NO)
3. **Win/Loss tagging** : Chaque trade est marque win ou loss
4. **Timing analysis** : On calcule le temps entre le trade et la resolution
5. **Scoring** :
   - Win rate > 70% avec > 10 trades = suspect
   - Timing moyen < 60 min avant resolution = tres suspect
   - Gros volumes + win rate eleve = potentiel insider
   - Le tout donne un "insider score" de 0 a 100

### Comment la detection sybil fonctionne

1. On prend tous les trades d'un marche
2. On cherche les wallets qui achetent le **meme outcome**
3. Dans une **fenetre de 5 minutes**
4. Si **3+ wallets** achetent la meme chose en meme temps = cluster
5. On cherche les wallets qui apparaissent dans **plusieurs clusters** = recidiviste

### Comment le copy trading genere des signaux

1. On prend la watchlist (wallets smart money)
2. On regarde leurs positions actuelles
3. Si **3+ smart wallets** sont sur le meme outcome avec > 60% de conviction :
   - Signal "SMART MONEY CONSENSUS"
   - Notification Telegram
4. Si un wallet de la watchlist fait un nouveau trade :
   - Notification immediate

---

## Combien de temps attendre

### Pour l'analyse de marche (analyze, signals, top)
**Immediat.** Ces commandes appellent l'API Polymarket en temps reel.

### Pour le momentum et les tendances
**Minimum 1-2 heures de monitoring.** Le momentum a besoin de points de donnees historiques. Plus tu laisses `monitor` tourner, plus c'est precis :
- 1h = RSI basique
- 6h = tendances fiables
- 24h+ = patterns solides

### Pour le wallet tracker
**10-30 minutes pour le premier index.** Ensuite :
- Premier `index` = constitue la base
- Relances regulieres = enrichit avec de nouveaux marches
- Apres 3-4 sessions d'indexation = leaderboard significatif

### Pour le copy trading
**Fonctionne des que la watchlist est peuplee.** Le flow :
1. `index 100` (20-30 min)
2. `watchlist auto` (1 sec)
3. `copytrade` (tourne en continu)

### Recommandation ideale
```bash
# Terminal 1 : monitoring continu (collecte + analyse)
python main.py monitor 60

# Terminal 2 (de temps en temps) : indexation de wallets
python main.py index 100

# Terminal 3 : copy trading
python main.py copytrade
```

---

## FAQ

### Ca coute quelque chose ?
Non. Toutes les APIs utilisees sont gratuites :
- Polymarket Gamma API : gratuit, pas de cle
- Polymarket CLOB API : gratuit, pas de cle
- Polygonscan API : gratuit (5 calls/sec)
- Telegram Bot API : gratuit

### Le bot trade automatiquement ?
Non. Le bot **detecte et alerte seulement**. C'est toi qui decides de trader ou non. L'execution automatique de trades n'est pas implementee (par choix de securite).

### C'est legal ?
Oui. Tout est base sur des donnees publiques :
- Les prix Polymarket sont publics
- Les trades on-chain sont publics (blockchain Polygon)
- L'analyse de donnees publiques est legale

### Comment ameliorer la precision ?
1. Laisse `monitor` tourner le plus longtemps possible
2. Fais des `index` reguliers avec des limites elevees
3. Ajoute une cle Polygonscan pour plus de donnees on-chain
4. Configure Telegram pour ne rien manquer

### Les donnees sont stockees ou ?
Dans `data/polymarket.db` (SQLite local). Tu peux le supprimer pour recommencer a zero. Le fichier grossit avec le temps mais reste gerable (quelques centaines de Mo max).

### Ca marche sur Windows/Mac/Linux ?
Oui, c'est du Python pur. `pip install -r requirements.txt` et c'est parti.

---

## Taille du code

| Module | Lignes | Role |
|--------|--------|------|
| main.py | ~1000 | CLI et affichage |
| analytics.py | ~480 | Moteur mathematique |
| wallet_tracker.py | ~500 | Tracking de wallets |
| smart_money.py | ~500 | Detection smart money |
| copy_trader.py | ~300 | Copy trading |
| blockchain.py | ~280 | Client Polygon |
| collector.py | ~310 | Collecteur SQLite |
| notifier.py | ~200 | Alertes Telegram |
| signals.py | ~230 | Moteur de signaux |
| scanner.py | ~100 | Scanner de base |
| analyzer.py | ~80 | Analyseur de base |
| client.py | ~65 | Client API |
| **Total** | **~4000+** | |
