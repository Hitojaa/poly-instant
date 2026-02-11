"""Client blockchain Polygon + Polygonscan pour les donnees on-chain Polymarket."""

import requests
import time
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict


class PolygonClient:
    """
    Client pour lire les donnees on-chain Polymarket sur Polygon.

    Sources de donnees :
    1. Polygonscan API - Historique des transactions, token transfers
    2. Polymarket CLOB API - Trades recents avec adresses maker/taker
    3. Polymarket Gamma API - Activity feed, resolutions de marches

    Le CTF Exchange contract de Polymarket sur Polygon :
    - CTF Exchange : 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E (Neg Risk CTF Exchange)
    - USDC sur Polygon : 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
    """

    # Polymarket contracts on Polygon
    CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
    USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

    POLYGONSCAN_URL = "https://api.polygonscan.com/api"
    CLOB_URL = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"

    def __init__(self, polygonscan_api_key=""):
        self.api_key = polygonscan_api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "poly-instant/0.2",
        })
        # Rate limiting
        self._last_call = 0
        self._min_interval = 0.25  # 4 calls/sec max pour Polygonscan free tier
        # Cache simple en memoire
        self._cache = {}
        self._cache_ttl = 60  # 60 secondes

    def _rate_limit(self):
        """Respecte le rate limit de Polygonscan."""
        now = time.time()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.time()

    def _cache_key(self, *args):
        return hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest()

    def _get_cached(self, key):
        if key in self._cache:
            data, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return data
        return None

    def _set_cache(self, key, data):
        self._cache[key] = (data, time.time())

    def _get(self, url, params=None, retries=3):
        """GET avec retry et rate limiting."""
        self._rate_limit()
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)

    # =========================================================================
    # POLYGONSCAN API
    # =========================================================================

    def _polygonscan(self, params):
        """Appel a l'API Polygonscan."""
        if self.api_key:
            params["apikey"] = self.api_key
        return self._get(self.POLYGONSCAN_URL, params=params)

    def get_wallet_transactions(self, address, start_block=0, end_block=99999999, page=1, offset=100):
        """
        Recupere les transactions d'un wallet sur Polygon.
        Utile pour voir l'activite globale d'un trader.
        """
        cache_key = self._cache_key("txs", address, page)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        result = self._polygonscan({
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": start_block,
            "endblock": end_block,
            "page": page,
            "offset": offset,
            "sort": "desc",
        })

        txs = result.get("result", [])
        if isinstance(txs, list):
            self._set_cache(cache_key, txs)
            return txs
        return []

    def get_erc20_transfers(self, address, contract=None, page=1, offset=100):
        """
        Recupere les transferts ERC-20 d'un wallet.
        Filtre optionnel par contrat (ex: USDC).
        """
        params = {
            "module": "account",
            "action": "tokentx",
            "address": address,
            "page": page,
            "offset": offset,
            "sort": "desc",
        }
        if contract:
            params["contractaddress"] = contract

        result = self._polygonscan(params)
        transfers = result.get("result", [])
        return transfers if isinstance(transfers, list) else []

    def get_erc1155_transfers(self, address, page=1, offset=100):
        """
        Recupere les transferts ERC-1155 d'un wallet.
        Les shares Polymarket sont des tokens ERC-1155 (Conditional Tokens).
        """
        result = self._polygonscan({
            "module": "account",
            "action": "token1155tx",
            "address": address,
            "page": page,
            "offset": offset,
            "sort": "desc",
        })
        transfers = result.get("result", [])
        return transfers if isinstance(transfers, list) else []

    def get_wallet_age(self, address):
        """Determine l'age d'un wallet (premiere transaction)."""
        txs = self.get_wallet_transactions(address, page=1, offset=1)
        if txs:
            # La derniere page donne la premiere tx quand sort=asc
            first_tx = self._polygonscan({
                "module": "account",
                "action": "txlist",
                "address": address,
                "page": 1,
                "offset": 1,
                "sort": "asc",
            })
            results = first_tx.get("result", [])
            if results and isinstance(results, list):
                ts = int(results[0].get("timeStamp", 0))
                return datetime.utcfromtimestamp(ts) if ts > 0 else None
        return None

    # =========================================================================
    # POLYMARKET CLOB API - TRADES
    # =========================================================================

    def get_trades(self, token_id=None, maker=None, market=None, limit=500):
        """
        Recupere les trades recents depuis le CLOB.
        Inclut les adresses maker/taker, prix, taille, timestamp.
        """
        params = {"limit": limit}
        if token_id:
            params["asset_id"] = token_id
        if maker:
            params["maker"] = maker
        if market:
            params["market"] = market

        cache_key = self._cache_key("trades", token_id, maker, market)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            result = self._get(f"{self.CLOB_URL}/trades", params=params)
            trades = result if isinstance(result, list) else result.get("trades", result.get("data", []))
            self._set_cache(cache_key, trades)
            return trades
        except Exception:
            return []

    def get_market_trades(self, condition_id, limit=500):
        """Recupere tous les trades pour un marche specifique."""
        return self.get_trades(market=condition_id, limit=limit)

    def get_wallet_trades(self, address, limit=500):
        """Recupere les trades d'un wallet specifique."""
        return self.get_trades(maker=address, limit=limit)

    # =========================================================================
    # POLYMARKET GAMMA API - ACTIVITY & RESOLUTIONS
    # =========================================================================

    def get_market_activity(self, slug=None, limit=100):
        """Recupere l'activite recente sur les marches."""
        params = {"limit": limit}
        if slug:
            params["slug"] = slug
        try:
            return self._get(f"{self.GAMMA_URL}/activity", params=params)
        except Exception:
            return []

    def get_resolved_markets(self, limit=50):
        """
        Recupere les marches recemment resolus.
        Crucial pour calculer le win rate des wallets.
        """
        params = {
            "limit": limit,
            "closed": "true",
            "order": "endDate",
            "ascending": "false",
        }
        try:
            return self._get(f"{self.GAMMA_URL}/markets", params=params)
        except Exception:
            return []

    def get_market_resolution(self, market):
        """
        Determine le resultat d'un marche resolu.
        Retourne 'YES', 'NO', ou None si pas encore resolu.
        """
        tokens = market.get("tokens", [])
        if len(tokens) != 2:
            return None

        # Un marche resolu a un token a prix 1.0 et l'autre a 0.0
        yes_price = float(tokens[0].get("price", 0.5))
        no_price = float(tokens[1].get("price", 0.5))

        if yes_price >= 0.95:
            return "YES"
        elif no_price >= 0.95:
            return "NO"

        # Verifier via le champ outcome si disponible
        outcome = market.get("outcome", market.get("resolution", ""))
        if outcome:
            return outcome.upper() if outcome.upper() in ("YES", "NO") else None

        return None

    # =========================================================================
    # BULK DATA COLLECTION
    # =========================================================================

    def collect_all_trades_for_market(self, market, max_pages=10):
        """
        Collecte TOUS les trades pour un marche (pagine).
        Retourne une liste de trades avec maker/taker addresses.
        """
        tokens = market.get("tokens", [])
        if len(tokens) != 2:
            return []

        all_trades = []
        for token in tokens:
            token_id = token.get("token_id", "")
            if not token_id:
                continue

            outcome = token.get("outcome", "Unknown")
            page_trades = self.get_trades(token_id=token_id, limit=500)
            for trade in page_trades:
                trade["_outcome"] = outcome
                trade["_token_id"] = token_id
            all_trades.extend(page_trades)

        return all_trades

    def extract_wallets_from_trades(self, trades):
        """
        Extrait tous les wallets uniques d'une liste de trades.
        Retourne un dict {address: [trades]}.
        """
        wallets = defaultdict(list)
        for trade in trades:
            # Les trades CLOB ont maker et taker
            maker = trade.get("maker_address", trade.get("maker", ""))
            taker = trade.get("taker_address", trade.get("taker", ""))

            if maker:
                wallets[maker.lower()].append({**trade, "_role": "maker"})
            if taker:
                wallets[taker.lower()].append({**trade, "_role": "taker"})

        return dict(wallets)
