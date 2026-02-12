"""Client blockchain Polygon + Polygonscan pour les donnees on-chain Polymarket."""

import requests
import time
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from .client import PolymarketClient


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
            if isinstance(result, list):
                trades = result
            elif isinstance(result, dict):
                # Essayer toutes les cles courantes
                trades = (result.get("trades") or result.get("data") or
                          result.get("results") or result.get("items") or [])
                if not isinstance(trades, list):
                    # Si une seule valeur est une liste, la prendre
                    for v in result.values():
                        if isinstance(v, list):
                            trades = v
                            break
                    else:
                        trades = []
            else:
                trades = []
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
        Essaie /markets puis fallback sur /events.
        """
        params = {
            "limit": limit,
            "closed": "true",
            "order": "endDate",
            "ascending": "false",
        }
        # Essai 1 : /markets
        try:
            data = self._get(f"{self.GAMMA_URL}/markets", params=params)
            markets = PolymarketClient._extract_list(data, "markets")
            if markets:
                return markets
        except Exception as e:
            print(f"[debug] resolved /markets failed: {e}", file=sys.stderr)

        # Essai 2 : /events fallback
        try:
            params_ev = {
                "limit": min(limit, 50),
                "closed": "true",
                "archived": "false",
                "order": "endDate",
                "ascending": "false",
            }
            data = self._get(f"{self.GAMMA_URL}/events", params=params_ev)
            events = PolymarketClient._extract_list(data, "events")
            markets = []
            for event in events:
                event_markets = event.get("markets", [])
                if isinstance(event_markets, list):
                    for m in event_markets:
                        if not m.get("slug") and event.get("slug"):
                            m["slug"] = event["slug"]
                        markets.append(m)
                else:
                    markets.append(event)
            return markets[:limit]
        except Exception as e:
            print(f"[debug] resolved /events fallback failed: {e}", file=sys.stderr)
            return []

    def get_market_resolution(self, market):
        """
        Determine le resultat d'un marche resolu.
        Retourne 'YES', 'NO', ou None si pas encore resolu.
        Utilise extract_prices() pour gerer tous les formats API.
        """
        # Verifier d'abord via le champ outcome/resolution (le plus fiable)
        outcome = market.get("outcome", market.get("resolution", ""))
        if outcome:
            up = outcome.upper().strip()
            if up in ("YES", "NO"):
                return up

        # Sinon verifier via les prix (un marche resolu = 1.0 / 0.0)
        yes_price, no_price, _, _ = PolymarketClient.extract_prices(market)
        if yes_price is not None and no_price is not None:
            if yes_price >= 0.95:
                return "YES"
            elif no_price >= 0.95:
                return "NO"

        return None

    # =========================================================================
    # BULK DATA COLLECTION
    # =========================================================================

    def collect_all_trades_for_market(self, market, max_pages=10):
        """
        Collecte TOUS les trades pour un marche (pagine).
        Retourne une liste de trades avec maker/taker addresses.
        Utilise extract_prices() pour gerer tous les formats API.
        """
        yes_price, no_price, yes_token_id, no_token_id = PolymarketClient.extract_prices(market)

        # Collecter les token IDs a interroger
        token_pairs = []
        if yes_token_id:
            token_pairs.append((yes_token_id, "YES"))
        if no_token_id:
            token_pairs.append((no_token_id, "NO"))

        # Fallback : essayer les tokens array classique aussi
        if not token_pairs:
            tokens = market.get("tokens", [])
            if isinstance(tokens, list):
                for t in tokens:
                    tid = t.get("token_id", "")
                    outcome = t.get("outcome", "Unknown")
                    if tid:
                        token_pairs.append((tid, outcome))

        if not token_pairs:
            # Dernier recours : essayer via condition_id
            condition_id = market.get("conditionId", market.get("condition_id", ""))
            if condition_id:
                trades = self.get_market_trades(condition_id, limit=500)
                for trade in trades:
                    trade["_outcome"] = trade.get("side", "Unknown").upper()
                    trade["_token_id"] = trade.get("asset_id", "")
                return trades
            return []

        all_trades = []
        for token_id, outcome in token_pairs:
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
