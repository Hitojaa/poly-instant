"""Client blockchain Polygon + Etherscan V2 pour les donnees on-chain Polymarket."""

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

    Sources de donnees (par priorite) :
    1. Polymarket CLOB API - Trades recents (marches actifs uniquement)
    2. Polymarket Gamma API - Activity feed, market metadata, resolutions
    3. Etherscan V2 API (chainid=137) - Historique on-chain ERC-1155, ERC-20

    Le CTF Exchange contract de Polymarket sur Polygon :
    - Neg Risk CTF Exchange : 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
    - Neg Risk Adapter : 0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296
    - CTF Conditional Tokens : 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
    - USDC sur Polygon : 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
    """

    # Polymarket contracts on Polygon
    CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
    CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

    # Etherscan V2 API (remplace Polygonscan) - ta cle Etherscan marche avec chainid=137
    ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
    CHAIN_ID = "137"  # Polygon PoS

    CLOB_URL = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"
    DATA_URL = "https://data-api.polymarket.com"

    def __init__(self, polygonscan_api_key=""):
        self.api_key = polygonscan_api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "poly-instant/0.2",
        })
        # Rate limiting
        self._last_call = 0
        self._min_interval = 0.25  # 4 calls/sec max
        # Cache simple en memoire
        self._cache = {}
        self._cache_ttl = 120  # 2 minutes

    def _rate_limit(self):
        """Respecte le rate limit."""
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

    def _get(self, url, params=None, retries=3, timeout=20):
        """GET avec retry et rate limiting."""
        self._rate_limit()
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)

    # =========================================================================
    # ETHERSCAN V2 API (anciennement Polygonscan)
    # =========================================================================

    def _etherscan(self, params):
        """Appel a l'API Etherscan V2 avec chainid=137 pour Polygon."""
        params["chainid"] = self.CHAIN_ID
        if self.api_key:
            params["apikey"] = self.api_key
        return self._get(self.ETHERSCAN_V2_URL, params=params, timeout=30)

    def get_wallet_transactions(self, address, start_block=0, end_block=99999999, page=1, offset=100):
        """Recupere les transactions d'un wallet sur Polygon."""
        cache_key = self._cache_key("txs", address, page)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        result = self._etherscan({
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
        """Recupere les transferts ERC-20 d'un wallet (ex: USDC)."""
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
        result = self._etherscan(params)
        transfers = result.get("result", [])
        return transfers if isinstance(transfers, list) else []

    def get_erc1155_transfers(self, address, page=1, offset=100):
        """
        Recupere les transferts ERC-1155 d'un wallet.
        Les shares Polymarket sont des tokens ERC-1155 (Conditional Tokens).
        """
        result = self._etherscan({
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
        first_tx = self._etherscan({
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
    # ON-CHAIN TRADE COLLECTION VIA ETHERSCAN
    # =========================================================================

    def get_exchange_erc1155_transfers(self, pages=3, offset=1000):
        """
        Recupere les transferts ERC-1155 recents sur le CTF Exchange.
        Ce sont les trades Polymarket on-chain.
        """
        cache_key = self._cache_key("exchange_1155", pages)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        all_transfers = []
        for page in range(1, pages + 1):
            try:
                result = self._etherscan({
                    "module": "account",
                    "action": "token1155tx",
                    "address": self.CTF_EXCHANGE,
                    "page": page,
                    "offset": offset,
                    "sort": "desc",
                })
                transfers = result.get("result", [])
                if isinstance(transfers, list):
                    all_transfers.extend(transfers)
                if not transfers or len(transfers) < offset:
                    break
            except Exception:
                break

        self._set_cache(cache_key, all_transfers)
        return all_transfers

    def get_onchain_trades_for_token(self, token_id, max_transfers=500):
        """
        Recupere les trades on-chain pour un token ID specifique.

        Utilise le CTF Contract (conditional tokens ERC-1155) au lieu
        du Exchange pour cibler directement les transfers de ce token.

        Retourne une liste de trades normalises.
        """
        if not token_id:
            return []

        # Convertir token_id en decimal si c'est un hex
        token_id_dec = str(token_id)
        try:
            if token_id.startswith("0x"):
                token_id_dec = str(int(token_id, 16))
            else:
                token_id_dec = token_id
        except (ValueError, TypeError):
            pass

        # Strategie : scanner les ERC-1155 transfers du CTF Contract
        # en filtrant par contractaddress (le token contract) pour reduire le volume
        trades = []

        try:
            result = self._etherscan({
                "module": "account",
                "action": "token1155tx",
                "contractaddress": self.CTF_CONTRACT,
                "address": self.CTF_EXCHANGE,
                "page": 1,
                "offset": min(max_transfers, 1000),
                "sort": "desc",
            })
            transfers = result.get("result", [])
            if isinstance(transfers, list):
                for tx in transfers:
                    tx_token = str(tx.get("tokenID", ""))
                    if tx_token == token_id_dec:
                        trade = self._transfer_to_trade(tx)
                        if trade:
                            trades.append(trade)
        except Exception as e:
            print(f"    [onchain] Etherscan error: {e}", flush=True)

        return trades

    def _transfer_to_trade(self, transfer):
        """Convertit un transfert ERC-1155 Etherscan en objet trade."""
        from_addr = transfer.get("from", "").lower()
        to_addr = transfer.get("to", "").lower()
        exchange_lower = self.CTF_EXCHANGE.lower()
        adapter_lower = self.NEG_RISK_ADAPTER.lower()

        # Determiner le trader et le cote
        # FROM exchange -> le trader a achete (il recoit les tokens)
        # TO exchange -> le trader a vendu (il renvoie les tokens)
        if from_addr in (exchange_lower, adapter_lower):
            trader = to_addr
            side = "BUY"
        elif to_addr in (exchange_lower, adapter_lower):
            trader = from_addr
            side = "SELL"
        else:
            # Transfert entre wallets (pas un trade direct)
            return None

        try:
            value = int(transfer.get("tokenValue", transfer.get("value", 0)))
            # Les conditional tokens ont 6 decimales (comme USDC)
            size = value / 1e6 if value > 1000 else float(value)
        except (ValueError, TypeError):
            size = 0

        if size <= 0:
            return None

        timestamp = ""
        try:
            ts = int(transfer.get("timeStamp", 0))
            if ts > 0:
                timestamp = datetime.utcfromtimestamp(ts).isoformat()
        except (ValueError, TypeError):
            pass

        # Essayer de calculer le prix reel via USDC dans la meme tx
        price = self._estimate_price_from_tx(transfer, size, side)

        return {
            "maker_address": trader,
            "taker_address": "",
            "price": price,
            "size": size,
            "side": side,
            "timestamp": timestamp,
            "match_time": timestamp,
            "tx_hash": transfer.get("hash", ""),
            "_source": "onchain",
            "_price_estimated": price == 0.5,  # Flag si prix est estim
        }

    def _estimate_price_from_tx(self, transfer, token_size, side):
        """
        Estime le prix d'un trade on-chain.
        Les ERC-1155 transfers ne contiennent pas le prix directement.
        On utilise le champ 'value' de la transaction ETH (si present) pour
        estimer, sinon on marque 0 (sera recalcule par wallet_tracker
        avec les donnees de resolution du marche).
        """
        # On ne peut pas recuperer le prix exact sans un appel API par tx
        # (trop lent/rate limited). On retourne 0 pour signaler "prix inconnu"
        # et le wallet_tracker recalculera le PnL via la resolution.
        return 0

    # =========================================================================
    # POLYMARKET CLOB API - TRADES
    # =========================================================================

    def get_trades(self, token_id=None, maker=None, market=None, limit=500):
        """
        Recupere les trades depuis le CLOB.
        Note : ne retourne que les trades recents des marches actifs.
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
                trades = (result.get("trades") or result.get("data") or
                          result.get("results") or result.get("items") or [])
                if not isinstance(trades, list):
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

        Essaie plusieurs champs car l'API Gamma n'est pas consistante.
        """
        # Champs possibles pour la resolution
        for field in ("outcome", "resolution", "winningOutcome", "resolvedOutcome"):
            val = market.get(field, "")
            if val:
                up = str(val).upper().strip()
                if up in ("YES", "NO"):
                    return up
                # Certains marches ont "1" pour YES, "0" pour NO
                if up in ("1", "TRUE"):
                    return "YES"
                if up in ("0", "FALSE"):
                    return "NO"

        # Check via les tokens (chaque token a un "winner" field)
        tokens = market.get("tokens", [])
        if isinstance(tokens, list):
            for t in tokens:
                winner = t.get("winner", t.get("winning", None))
                if winner is True or winner == "true":
                    outcome = t.get("outcome", "").upper()
                    if outcome in ("YES", "NO"):
                        return outcome

        # Fallback : verifier via les prix (un marche resolu = 1.0 / 0.0)
        yes_price, no_price, _, _ = PolymarketClient.extract_prices(market)
        if yes_price is not None and no_price is not None:
            if yes_price >= 0.95:
                return "YES"
            elif no_price >= 0.95:
                return "NO"

        return None

    # =========================================================================
    # BULK DATA COLLECTION - Multi-strategie
    # =========================================================================

    def collect_all_trades_for_market(self, market, max_pages=10, verbose=True):
        """
        Collecte les trades pour un marche via multiple strategies :
        1. CLOB API (trades recents, marches actifs)
        2. Gamma API activity (historique via API Polymarket)
        3. On-chain ERC-1155 transfers via Etherscan (historique permanent)
        """
        yes_price, no_price, yes_token_id, no_token_id = PolymarketClient.extract_prices(market)
        condition_id = market.get("conditionId", market.get("condition_id", ""))
        slug = market.get("slug", "")

        # Collecter les token IDs
        token_pairs = []
        if yes_token_id:
            token_pairs.append((yes_token_id, "YES"))
        if no_token_id:
            token_pairs.append((no_token_id, "NO"))

        # Fallback tokens array
        if not token_pairs:
            tokens = market.get("tokens", [])
            if isinstance(tokens, list):
                for t in tokens:
                    tid = t.get("token_id", "")
                    outcome = t.get("outcome", "Unknown")
                    if tid:
                        token_pairs.append((tid, outcome))

        if not token_pairs:
            if verbose:
                print(f"    [skip] Pas de token IDs pour {slug[:40]}", flush=True)
            return []

        # ---- STRATEGIE 1 : CLOB API (rapide) ----
        trades = self._try_clob_trades(token_pairs, condition_id)
        if trades:
            if verbose:
                print(f"    [clob] {len(trades)} trades", flush=True)
            return trades

        # ---- STRATEGIE 2 : Data API activity (historique des trades) ----
        trades = self._try_data_api_activity(slug, token_pairs)
        if trades:
            if verbose:
                print(f"    [data-api] {len(trades)} trades", flush=True)
            return trades

        # ---- STRATEGIE 3 : Gamma API activity (fallback) ----
        trades = self._try_gamma_activity(slug, token_pairs)
        if trades:
            if verbose:
                print(f"    [gamma] {len(trades)} trades", flush=True)
            return trades

        # ---- STRATEGIE 4 : On-chain ERC-1155 via Etherscan V2 ----
        if self.api_key and token_pairs:
            trades = self._try_onchain_trades(token_pairs)
            if trades:
                if verbose:
                    print(f"    [onchain] {len(trades)} trades", flush=True)
                return trades

        return []

    def _try_clob_trades(self, token_pairs, condition_id):
        """Strategie 1 : trades via CLOB API."""
        all_trades = []

        # Par token_id
        for token_id, outcome in token_pairs:
            page_trades = self.get_trades(token_id=token_id, limit=500)
            for trade in page_trades:
                trade["_outcome"] = outcome
                trade["_token_id"] = token_id
            all_trades.extend(page_trades)

        # Par condition_id si token_id n'a rien donne
        if not all_trades and condition_id:
            clob_trades = self.get_market_trades(condition_id, limit=500)
            for trade in clob_trades:
                trade["_outcome"] = trade.get("side", "Unknown").upper()
                trade["_token_id"] = trade.get("asset_id", "")
            all_trades.extend(clob_trades)

        return all_trades

    def _try_data_api_activity(self, slug, token_pairs):
        """Strategie 2 : trades via Polymarket Data API (historique complet)."""
        if not slug:
            return []

        try:
            # L'endpoint /activity du Data API retourne l'historique des trades
            data = self._get(f"{self.DATA_URL}/activity", params={
                "slug": slug,
                "limit": 500,
            }, timeout=15)

            activities = data if isinstance(data, list) else (
                data.get("data", data.get("activity", data.get("results", [])))
                if isinstance(data, dict) else []
            )

            if not isinstance(activities, list) or not activities:
                return []

            # Construire un mapping token_id -> outcome
            token_outcome_map = {}
            for tid, outcome in token_pairs:
                token_outcome_map[tid] = outcome

            trades = []
            for act in activities:
                asset = act.get("asset_id", act.get("tokenId", act.get("asset", "")))
                outcome = token_outcome_map.get(asset, act.get("outcome", "YES")).upper()

                trade = {
                    "maker_address": act.get("proxyWallet", act.get("address", act.get("user", ""))),
                    "taker_address": "",
                    "price": float(act.get("price", act.get("outcomePrice", 0))),
                    "size": float(act.get("amount", act.get("size", act.get("value", 0)))),
                    "side": act.get("side", act.get("type", "")),
                    "timestamp": act.get("timestamp", act.get("createdAt", act.get("created_at", ""))),
                    "_outcome": outcome,
                    "_token_id": asset,
                    "_source": "data_api",
                }
                if trade["maker_address"] and trade["size"] > 0:
                    trades.append(trade)

            return trades

        except Exception:
            return []

    def _try_gamma_activity(self, slug, token_pairs):
        """Strategie 3 : activity feed via Gamma API (fallback)."""
        if not slug:
            return []

        try:
            data = self._get(f"{self.GAMMA_URL}/activity", params={
                "slug": slug,
                "limit": 500,
            })

            activities = data if isinstance(data, list) else (
                data.get("data", data.get("activity", data.get("results", [])))
                if isinstance(data, dict) else []
            )

            if not isinstance(activities, list) or not activities:
                return []

            # Convertir les activites en trades
            trades = []
            for act in activities:
                trade = {
                    "maker_address": act.get("address", act.get("user", act.get("proxyWallet", ""))),
                    "taker_address": "",
                    "price": float(act.get("price", act.get("outcomePrice", 0.5))),
                    "size": float(act.get("amount", act.get("size", act.get("value", 0)))),
                    "side": act.get("side", act.get("type", "")),
                    "timestamp": act.get("timestamp", act.get("createdAt", act.get("created_at", ""))),
                    "_outcome": act.get("outcome", act.get("outcomeIndex", "YES")).upper(),
                    "_token_id": act.get("asset_id", act.get("tokenId", "")),
                    "_source": "gamma_activity",
                }
                if trade["maker_address"] and trade["size"] > 0:
                    trades.append(trade)

            return trades

        except Exception:
            return []

    def _try_onchain_trades(self, token_pairs):
        """Strategie 4 : trades on-chain via Etherscan V2 ERC-1155 transfers."""
        all_trades = []

        for token_id, outcome in token_pairs:
            onchain = self.get_onchain_trades_for_token(token_id, max_transfers=500)
            for trade in onchain:
                trade["_outcome"] = outcome
                trade["_token_id"] = token_id
            all_trades.extend(onchain)
            if all_trades:
                break  # On a assez de data, pas besoin du 2eme token

        return all_trades

    def extract_wallets_from_trades(self, trades):
        """
        Extrait tous les wallets uniques d'une liste de trades.
        Retourne un dict {address: [trades]}.
        """
        wallets = defaultdict(list)
        for trade in trades:
            maker = trade.get("maker_address", trade.get("maker", ""))
            taker = trade.get("taker_address", trade.get("taker", ""))

            if maker:
                wallets[maker.lower()].append({**trade, "_role": "maker"})
            if taker:
                wallets[taker.lower()].append({**trade, "_role": "taker"})

        return dict(wallets)
