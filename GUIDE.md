# poly-instant - Guide Complet

## Polymarket Intelligence Engine - Analyse avancee, Smart Money Tracking & Copy Trading

---

## Table des matieres

1. [Vue d'ensemble](#vue-densemble)
2. [Installation](#installation)
3. [Configuration des APIs](#configuration-des-apis)
4. [Architecture du code](#architecture-du-code)
5. [Toutes les commandes](#toutes-les-commandes)
6. [Guide pas a pas : du premier scan au copy trading](#guide-pas-a-pas)
7. [Comment ca marche en detail](#comment-ca-marche-en-detail)
8. [Combien de temps attendre avant de trader](#combien-de-temps-attendre)
9. [Troubleshooting](#troubleshooting)
10. [FAQ](#faq)

---

## Vue d'ensemble

poly-instant est un moteur d'intelligence pour Polymarket qui combine :

- **Analyse de marche avancee** : Kelly Criterion, Expected Value, probabilites bayesiennes, momentum/RSI, detection de baleines, profil de volume
- **Wallet Tracker** : indexe les trades on-chain, score les wallets par win rate/PnL/timing, identifie les smart wallets
- **Smart Money Detection** : detecte les wallets qui achetent avant les bonnes resolutions, clusters sybil, activite anormale
- **Copy Trading** : surveille les smart wallets en temps reel et envoie des alertes Telegram quand ils tradent
- **Notifications Telegram** : alertes formatees pour chaque type de signal

### Ce que le programme fait concretement

1. Scanne les marches Polymarket en continu via l'API Gamma + CLOB
2. Collecte les prix, orderbooks et volumes dans une base SQLite locale
3. Analyse chaque marche avec des maths poussees (Kelly, EV, Bayes, RSI...)
4. Indexe les trades des marches resolus pour identifier qui gagne le plus
5. Score chaque wallet : win rate, timing, volume, PnL, insider score
6. Detecte les clusters de wallets suspects (sybil) et l'activite anormale
7. Genere des signaux de trading prioritises avec niveau de confiance
8. Envoie des alertes Telegram en temps reel

### Sources de donnees

| Source | Quoi | Cle requise |
|--------|------|-------------|
| Gamma API | Marches, prix, metadata, activite | Non (gratuit) |
| CLOB API | Orderbooks, trades recents, spreads | Non (gratuit) |
| Etherscan V2 | Trades on-chain historiques (ERC-1155) | Oui (gratuit) |
| Telegram Bot | Envoi de notifications | Oui (gratuit) |

---

## Installation

```bash
# 1. Cloner le repo
git clone <repo-url>
cd poly-instant

# 2. Creer un environnement virtuel (recommande)
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Mac/Linux

# 3. Installer les dependances
pip install -r requirements.txt

# 4. Copier et configurer le .env
cp .env.example .env
# Editer .env avec tes cles (voir section suivante)

# 5. Tester que ca marche
python main.py markets
```

### Dependances

- `requests` : appels API (Gamma, CLOB, Etherscan)
- `python-dotenv` : variables d'environnement (.env)
- `tabulate` : tableaux formattes en CLI
- `pandas` : manipulation de donnees

Tout est en **Python pur**, pas besoin de Node.js ou autre.

---

## Configuration des APIs

### Fichier `.env`

```bash
# === TELEGRAM (optionnel mais recommande) ===
TELEGRAM_BOT_TOKEN=ton_token_ici
TELEGRAM_CHAT_ID=ton_chat_id_ici

# === ETHERSCAN V2 (RECOMMANDE pour wallet tracker + on-chain) ===
# Ta cle Etherscan marche pour Polygon (chainid=137)
POLYGONSCAN_API_KEY=ta_cle_etherscan_ici

# === CONFIG ===
SCAN_INTERVAL=60        # Intervalle de scan en secondes
MIN_PROFIT_PCT=2.0      # Profit minimum pour les alertes d'arbitrage
```

### Setup Etherscan API V2 (2 minutes) - IMPORTANT

Polygonscan utilise maintenant **Etherscan API V2**. Une seule cle marche pour Polygon + 60 autres chains.

1. Va sur **https://etherscan.io** (PAS polygonscan.com)
2. Cree un compte gratuit
3. Va dans **My Account > API Keys > Add**
4. Copie la cle dans `POLYGONSCAN_API_KEY` de ton `.env`

**Pourquoi c'est important ?** Sans cette cle, le wallet tracker ne peut pas recuperer les trades historiques on-chain des marches resolus. L'indexation (`index`) a besoin de cette cle pour fonctionner a 100%.

Le free tier donne 5 calls/sec, c'est largement suffisant.

### Setup Telegram (5 minutes) - optionnel

1. Ouvre Telegram, cherche **@BotFather**
2. Envoie `/newbot`
3. Donne un nom a ton bot (ex: "PolyInstant Alerts")
4. BotFather te donne un **token** -> copie-le dans `TELEGRAM_BOT_TOKEN`
5. Envoie un message a ton bot (n'importe quoi, juste pour initier le chat)
6. Va sur `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
7. Cherche `"chat":{"id":XXXXXXX}` -> copie le nombre dans `TELEGRAM_CHAT_ID`

**Sans Telegram**, tout fonctionne en CLI. Tu ne recevras juste pas de notifications push.

---

## Architecture du code

```
poly-instant/
├── main.py                      # CLI principal - 16 commandes
├── requirements.txt             # Dependances Python
├── .env                         # Tes cles API (gitignore)
├── .env.example                 # Template de config
├── GUIDE.md                     # Ce fichier
├── data/
│   └── polymarket.db            # Base SQLite (creee auto)
└── polyinstant/
    ├── __init__.py
    │
    │   --- COUCHE API ---
    ├── client.py                # Client Polymarket (Gamma + CLOB)
    │                            #   extract_prices() : gere tous les formats API
    │                            #   get_markets(), get_events(), search_markets()
    │                            #   get_orderbook(), get_price(), get_midpoint()
    │
    ├── blockchain.py            # Client Etherscan V2 + on-chain
    │                            #   3 strategies de collecte de trades :
    │                            #   1. CLOB API (marches actifs)
    │                            #   2. Gamma /activity (historique)
    │                            #   3. On-chain ERC-1155 via Etherscan (permanent)
    │                            #   get_resolved_markets(), get_market_resolution()
    │                            #   get_onchain_trades_for_token()
    │
    │   --- COUCHE ANALYSE ---
    ├── analytics.py             # Moteur mathematique avance
    │                            #   Kelly Criterion, Expected Value
    │                            #   Bayesian probability updates
    │                            #   Momentum / RSI / Volatilite
    │                            #   Detection de baleines
    │                            #   Volume profile (POC, Value Area)
    │                            #   Score d'efficience du marche
    │                            #   MarketPredictor (prediction directionnelle)
    │
    ├── scanner.py               # Scanner d'arbitrage et mispricing
    ├── analyzer.py              # Analyse basique d'un marche
    │
    │   --- COUCHE DONNEES ---
    ├── collector.py             # Collecteur SQLite
    │                            #   Snapshots de prix en continu
    │                            #   Historique d'orderbook
    │                            #   Log d'evenements
    │
    │   --- COUCHE INTELLIGENCE ---
    ├── wallet_tracker.py        # Systeme de tracking de wallets
    │                            #   Indexation des trades resolus
    │                            #   Scoring multi-facteurs (8 metriques)
    │                            #   Leaderboard, profils, watchlist
    │
    ├── smart_money.py           # Detection smart money
    │                            #   Activite pre-resolution
    │                            #   Clusters sybil (fenetre glissante)
    │                            #   Activite anormale
    │                            #   Analyse reseau de wallets
    │                            #   Smart money flow par marche
    │
    ├── copy_trader.py           # Copy trading temps reel
    │                            #   Monitoring de la watchlist
    │                            #   Consensus smart money
    │                            #   Alertes de trades
    │
    │   --- COUCHE NOTIFICATION ---
    ├── signals.py               # Moteur de signaux composite
    │                            #   Combine tous les indicateurs
    │                            #   Scoring et prioritisation
    │
    └── notifier.py              # Alertes Telegram formatees
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
| `wallet_trades` | Historique trade par trade, win/loss, PnL |
| `wallet_scores` | Scores calcules (composite, insider, timing...) |
| `tracked_wallets` | Watchlist de wallets a suivre |

**Pour reset** : supprime `data/polymarket.db` et relance.

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

### Monitoring Continu

| Commande | Description |
|----------|-------------|
| `python main.py monitor [sec]` | Mode continu : collecte + analyse + alertes Telegram (defaut: 60s) |
| `python main.py history <slug> [hours]` | Historique des prix d'un marche |
| `python main.py stats` | Statistiques completes de la DB |

### Wallet Tracker & Smart Money

| Commande | Description |
|----------|-------------|
| `python main.py index [limit]` | **Indexe les wallets depuis les marches resolus** |
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

### Tris disponibles pour `leaderboard`

| Tri | Description |
|-----|-------------|
| `composite_score` | Score global (defaut) |
| `win_rate` | Taux de reussite brut |
| `total_pnl` | Profit/Perte total |
| `insider_score` | Score d'insider (timing + win rate + volume) |
| `sharpe_ratio` | Ratio de Sharpe (rendement ajuste au risque) |

---

## Guide pas a pas

### Etape 1 : Premier lancement (immediat, pas de cle requise)

```bash
# Voir les marches actifs
python main.py markets

# Voir les top marches par volume
python main.py top 20

# Scanner les arbitrages
python main.py scan
```

Ces commandes marchent immediatement sans aucune configuration. Elles utilisent l'API Polymarket gratuite.

### Etape 2 : Analyser un marche en profondeur (immediat)

```bash
# Copie le slug d'un marche depuis l'URL Polymarket
# Ex: polymarket.com/event/will-trump-win-2028 -> slug = will-trump-win-2028
python main.py analyze will-trump-win-2028
```

L'analyse te donne :
- **Prix et spread** - YES/NO, cout total, spread bid/ask
- **Probabilite bayesienne** - ajustee par volume, liquidite, momentum
- **Kelly Criterion** - combien miser (full/half/quarter kelly)
- **Expected Value** - EV absolue, ROI potentiel, breakeven
- **Efficience** - est-ce que le marche est correctement price ?
- **Volume profile** - supports/resistances, POC, pression achat/vente
- **Baleines** - gros ordres detectes, biais directionnel
- **Smart money flow** - direction des smart wallets (si DB indexee)
- **Prediction** - direction et confiance aggregees

### Etape 3 : Lancer le monitoring (laisser tourner)

```bash
# Scan toutes les 60 secondes (defaut)
python main.py monitor

# Scan toutes les 30 secondes (plus reactif)
python main.py monitor 30
```

**Laisse tourner en arriere-plan.** Le monitoring :
- Collecte les prix et orderbooks de 100 marches
- Detecte les arbitrages, baleines, momentum
- Envoie des alertes Telegram (si configure)
- Stocke l'historique dans SQLite

Plus ca tourne longtemps, plus c'est precis :
- **1h** = RSI basique, premieres alertes
- **6h** = tendances fiables, momentum precis
- **24h+** = patterns solides, alertes pertinentes

### Etape 4 : Construire la DB de wallets (necessite cle Etherscan)

C'est LA etape cle pour le smart money tracking. Elle indexe les trades des marches resolus pour identifier qui gagne.

```bash
# Indexer 100 marches resolus recemment
python main.py index 100
```

**Ce qui se passe en coulisses :**

1. Recupere les 100 derniers marches resolus sur Polymarket (Gamma API)
2. Pour chaque marche, determine le resultat (YES ou NO)
3. Collecte tous les trades via 3 strategies :
   - **CLOB API** : trades recents (marches encore actifs)
   - **Gamma /activity** : historique d'activite par marche
   - **Etherscan on-chain** : transferts ERC-1155 sur le CTF Exchange (permanent)
4. Pour chaque trade, calcule win/loss et PnL
5. Score chaque wallet (composite de 8 metriques)
6. Marque les "smart wallets" (score >= 70/100)

**Relance regulierement** pour enrichir la DB :
```bash
# Chaque jour ou quelques jours
python main.py index 100
```

Le debug te montre la progression :
```
[debug] Marches resolus recuperes: 100
[debug] Marche OK: will-trump-win res=YES trades=342 src=onchain
[debug] Resume: resolus=100 skip_no_res=5 skip_dup=12 skip_no_trades=30 indexed=1847
```

### Etape 5 : Explorer les wallets

```bash
# Voir le leaderboard global
python main.py leaderboard

# Top 30 par win rate
python main.py leaderboard 30 win_rate

# Top par insider score (les plus suspects)
python main.py leaderboard 30 insider_score

# Profil detaille d'un wallet
python main.py wallet 0x1234abcd...

# Voir son reseau de wallets lies
python main.py network 0x1234abcd...
```

### Etape 6 : Detection smart money

```bash
# Scan complet (pre-resolution + sybil + abnormal)
python main.py smartmoney

# Qui a achete juste avant les resolutions ?
python main.py smartmoney pre
# Fenetre de 30 minutes (plus strict)
python main.py smartmoney pre 30

# Clusters sybil (wallets coordonnes)
python main.py smartmoney sybil

# Activite anormale des dernieres 24h
python main.py smartmoney abnormal

# Smart money flow sur un marche specifique
python main.py smartmoney flow will-trump-win-2028
```

### Etape 7 : Copy trading

```bash
# 1. Peupler la watchlist avec les meilleurs wallets
python main.py watchlist auto

# Ou ajouter manuellement un wallet
python main.py watchlist add 0x1234... "Whale spotted on BTC market"

# 2. Voir la watchlist
python main.py watchlist

# 3. Lancer le copy-trading
python main.py copytrade
```

Le copy trader :
- Surveille les positions des wallets de ta watchlist
- Detecte quand 3+ smart wallets convergent sur le meme outcome
- Envoie une notification Telegram "SMART MONEY CONSENSUS"
- Re-scanne periodiquement pour de nouveaux trades

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

On utilise le **half-kelly** (plus conservateur) par defaut. Le quarter-kelly est recommande pour les debutants.

#### Expected Value (EV)
```
EV = (prob_win * payout_win) + (prob_lose * payout_lose) - cost
```
Sur Polymarket : payout_win = 1.0 (moins 2% de frais), payout_lose = 0.
Un trade avec **EV > 0** est profitable en esperance mathematique.

#### Bayesian Update
Met a jour la probabilite du marche en fonction de :
- Volume 24h (plus de volume = prix plus fiable)
- Liquidite (plus liquide = moins manipulable)
- Momentum du prix (tendance recente)
- Desequilibre du carnet d'ordres (pression achat/vente)

#### RSI (Relative Strength Index)
```
RSI = 100 - (100 / (1 + RS))
RS = moyenne des gains / moyenne des pertes
```
- **RSI > 70** = surachete (potentiel de baisse, le prix est monte trop vite)
- **RSI < 30** = survendu (potentiel de hausse, le prix est tombe trop vite)

#### Sharpe Ratio (scoring wallets)
```
Sharpe = rendement_moyen / ecart_type_des_rendements
```
Un Sharpe eleve = gains reguliers avec peu de variance = **bon trader consistant**.

### Comment le wallet tracker score les wallets

Chaque wallet recoit un **score composite de 0 a 100** base sur 8 metriques :

| Metrique | Poids | Description |
|----------|-------|-------------|
| Win rate | 25% | Pourcentage de trades gagnants |
| Sharpe ratio | 15% | Regularite des gains (ajuste au risque) |
| Timing score | 20% | Combien de temps avant la resolution |
| Consistency | 10% | Regularite a travers les marches |
| Volume score | 10% | Taille des positions (log scale) |
| Insider score | 20% | Composite timing + win rate + gros trades |

#### Tiers de wallets

| Tier | Score | Signification |
|------|-------|---------------|
| S - ELITE | >= 85 | Top performers, potentiels insiders |
| A - SMART MONEY | >= 70 | Traders tres performants |
| B - ABOVE AVERAGE | >= 55 | Au dessus de la moyenne |
| C - AVERAGE | >= 40 | Trader moyen |
| D - BELOW AVERAGE | < 40 | En dessous de la moyenne |

### Comment la detection d'insiders fonctionne

1. **Indexation** : on collecte TOUS les trades des marches resolus
2. **Resolution mapping** : on sait quel outcome a gagne (YES ou NO)
3. **Win/Loss tagging** : chaque trade est marque gagnant ou perdant
4. **Timing analysis** : on calcule le temps entre le trade et la resolution
5. **Scoring** :
   - Win rate > 70% avec > 10 trades = suspect
   - Timing moyen < 60 min avant resolution = tres suspect
   - Gros volumes + win rate eleve = potentiel insider
   - Le tout donne un "insider score" de 0 a 100

### Comment la detection sybil fonctionne

Un sybil = une personne qui utilise plusieurs wallets pour manipuler.

1. On prend tous les trades d'un marche
2. On cherche les wallets qui achetent le **meme outcome**
3. Dans une **fenetre de 5 minutes** (trades presque simultanes)
4. Si **3+ wallets** achetent la meme chose en meme temps = **cluster detecte**
5. On cherche les wallets qui apparaissent dans **plusieurs clusters** = recidiviste

### Comment le copy trading genere des signaux

1. On prend la watchlist (tes wallets smart money)
2. On regarde leurs positions actuelles sur les marches actifs
3. Si **3+ smart wallets** sont sur le meme outcome avec **> 60% de conviction** :
   - Signal **"SMART MONEY CONSENSUS"**
   - Notification Telegram immediate
4. Si un wallet de la watchlist fait un nouveau trade :
   - Notification immediate avec details

### Comment la collecte de trades on-chain fonctionne

Les trades Polymarket passent par des smart contracts sur la blockchain Polygon :

1. **NegRiskCtfExchange** (`0x4bFb41...`) - le contrat d'echange principal
2. **Conditional Tokens** (`0x4D97DC...`) - les tokens ERC-1155 qui representent les positions

Quand quelqu'un achete des shares YES :
- Des tokens ERC-1155 sont transferes **du** contrat d'echange **vers** le wallet du trader
- On detecte ca via Etherscan V2 API (`token1155tx`)

Quand quelqu'un vend :
- Les tokens sont transferes **vers** le contrat d'echange
- On detecte aussi

Avantage : les donnees on-chain sont **permanentes** et **completes**, contrairement au CLOB qui ne garde que les trades recents.

---

## Combien de temps attendre

### Pour les commandes d'analyse (analyze, signals, top, markets)
**Immediat.** Ces commandes appellent l'API Polymarket en temps reel. Aucune attente.

### Pour le momentum et les tendances
**Minimum 1-2 heures de monitoring.** Plus c'est long, plus c'est precis :

| Duree monitoring | Qualite |
|-----------------|---------|
| 30 min | Donnees de base, pas de RSI fiable |
| 1-2h | RSI basique, premieres tendances |
| 6h | Tendances fiables, momentum precis |
| 24h+ | Patterns solides, signaux haute qualite |

### Pour le wallet tracker
**10-30 minutes pour le premier `index`.** Ensuite :

| Action | Temps | Resultat |
|--------|-------|----------|
| `index 50` | 5-10 min | Base initiale de wallets |
| `index 100` | 10-20 min | Leaderboard significatif |
| `index 200` | 20-40 min | Analyse approfondie |
| 3-4 sessions d'index | Sur quelques jours | Database complete |

### Pour le copy trading
**Fonctionne des que la watchlist est peuplee.**

### Setup complet recommande

```bash
# Terminal 1 : monitoring continu (collecte + analyse + alertes)
python main.py monitor 60

# Terminal 2 (de temps en temps) : enrichir la DB de wallets
python main.py index 100

# Terminal 3 : copy trading (quand la watchlist est prete)
python main.py copytrade
```

**Timeline realiste pour un setup complet :**

1. **Jour 1** : `markets`, `top`, `analyze` pour explorer
2. **Jour 1** : Lancer `monitor` en arriere-plan
3. **Jour 1** : Premier `index 100` pour constituer la DB
4. **Jour 2** : `leaderboard`, `smartmoney` pour analyser les wallets
5. **Jour 2** : `watchlist auto` + `copytrade` pour commencer le suivi
6. **Jour 3+** : Re-`index`, affiner la watchlist, profiter des alertes

---

## Troubleshooting

### "0 marches resolus recuperes"
- Verifie ta connexion internet
- L'API Gamma peut etre temporairement down
- Le code essaie automatiquement `/markets` puis `/events` en fallback

### "0 trades indexes" (apres avoir recupere des marches)
- **Verifie ta cle Etherscan** dans `.env` (`POLYGONSCAN_API_KEY=...`)
- La cle doit etre generee sur **etherscan.io** (pas polygonscan.com)
- Sans cle, seules les strategies CLOB et Gamma activity sont disponibles
- Le CLOB ne garde pas les trades des marches fermes, donc la cle Etherscan est quasi-indispensable

### "HTTPSConnectionPool... 403 Forbidden"
- C'est un probleme de proxy/firewall, pas du code
- Verifie que ton reseau permet les connexions vers `gamma-api.polymarket.com` et `api.etherscan.io`

### "Aucun signal" ou "Pas assez d'historique"
- Lance `monitor` et laisse tourner au moins 1-2 heures
- Les signaux de momentum ont besoin de points de donnees historiques

### La DB est corrompue ou bizarre
- Supprime `data/polymarket.db` et relance
- Tout sera recree automatiquement

---

## FAQ

### Ca coute quelque chose ?
Non. Toutes les APIs sont gratuites :
- Polymarket Gamma API : gratuit, pas de cle
- Polymarket CLOB API : gratuit, pas de cle
- Etherscan V2 API : gratuit (5 calls/sec en free tier)
- Telegram Bot API : gratuit

### Le bot trade automatiquement ?
**Non.** Le bot detecte et alerte seulement. C'est toi qui decides de trader. L'execution automatique de trades n'est pas implementee (choix de securite).

### C'est legal ?
Oui. Tout est base sur des donnees publiques :
- Les prix Polymarket sont publics
- Les trades on-chain sont publics (blockchain Polygon)
- L'analyse de donnees publiques est legale

### Comment ameliorer la precision ?
1. Laisse `monitor` tourner le plus longtemps possible
2. Fais des `index` reguliers avec des limites elevees (100-200)
3. **Configure ta cle Etherscan** pour les donnees on-chain historiques
4. Configure Telegram pour ne rien manquer
5. Enrichis ta watchlist avec les wallets les plus performants

### Les donnees sont stockees ou ?
Dans `data/polymarket.db` (SQLite local). Le fichier grossit avec le temps mais reste gerable (quelques centaines de Mo max). Supprime-le pour recommencer a zero.

### Ca marche sur Windows / Mac / Linux ?
Oui, c'est du Python pur. `pip install -r requirements.txt` et c'est parti. Teste sur Windows (PowerShell) et Linux.

### Quel est le slug d'un marche ?
C'est la partie de l'URL Polymarket apres `/event/`. Exemple :
- URL : `https://polymarket.com/event/will-trump-win-2028`
- Slug : `will-trump-win-2028`

### Difference entre `monitor` et `copytrade` ?
- **`monitor`** : scanne TOUS les marches pour des signaux (arbitrage, baleines, momentum). C'est l'analyse de marche.
- **`copytrade`** : surveille les WALLETS de ta watchlist. C'est le suivi de personnes.

Les deux sont complementaires. Lance `monitor` dans un terminal et `copytrade` dans un autre.

---

## Taille du code

| Module | Lignes | Role |
|--------|--------|------|
| main.py | ~1035 | CLI et affichage |
| blockchain.py | ~600 | Client Etherscan V2 + on-chain |
| wallet_tracker.py | ~775 | Tracking et scoring de wallets |
| smart_money.py | ~500 | Detection smart money + sybil |
| analytics.py | ~480 | Moteur mathematique |
| copy_trader.py | ~300 | Copy trading temps reel |
| collector.py | ~250 | Collecteur SQLite |
| signals.py | ~230 | Moteur de signaux |
| notifier.py | ~200 | Alertes Telegram |
| scanner.py | ~100 | Scanner arbitrage |
| client.py | ~240 | Client API Polymarket |
| analyzer.py | ~80 | Analyseur basique |
| **Total** | **~4800+** | |
