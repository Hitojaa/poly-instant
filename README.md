# poly-instant

Polymarket scanner & trading toolkit.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Usage

```bash
# Scanner d'opportunites
python main.py scan

# Analyser un marche specifique
python main.py analyze <market_slug>

# Lister les marches actifs
python main.py markets
```

## Notes

- Lecture des donnees : aucune cle API requise
- Trading : necessite un wallet Polygon + USDC + cles API Polymarket
- France : VPN necessaire pour acceder a Polymarket
