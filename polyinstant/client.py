"""Client pour les APIs Polymarket (Gamma + CLOB)."""

import requests
import time
import sys


class PolymarketClient:
    """Wrapper autour des APIs publiques Polymarket."""

    GAMMA_URL = "https://gamma-api.polymarket.com"
    CLOB_URL = "https://clob.polymarket.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "poly-instant/0.2",
        })

    def _get(self, url, params=None, retries=3):
        """GET with basic retry logic."""
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)

    @staticmethod
    def _extract_list(data, key=None):
        """Extrait une liste depuis la reponse API (gere list ou dict)."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Essayer les cles courantes
            for k in (key, "data", "markets", "events", "results", "items"):
                if k and k in data and isinstance(data[k], list):
                    return data[k]
            # Si le dict a une seule cle qui contient une liste
            for v in data.values():
                if isinstance(v, list):
                    return v
        return []

    # -- Gamma API (market metadata) --

    def get_markets(self, limit=50, active=True, closed=False):
        """Fetch active markets from Gamma API."""
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "archived": "false",
        }
        try:
            data = self._get(f"{self.GAMMA_URL}/markets", params=params)
            markets = self._extract_list(data, "markets")
            if markets:
                return markets
        except Exception as e:
            print(f"[debug] /markets failed: {e}", file=sys.stderr)

        # Fallback: essayer via /events (recommande par Polymarket)
        return self._get_markets_via_events(limit=limit, active=active, closed=closed)

    def _get_markets_via_events(self, limit=50, active=True, closed=False):
        """Fallback : recupere les marches via l'endpoint /events."""
        params = {
            "limit": min(limit, 50),
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "archived": "false",
            "order": "volume24hr",
            "ascending": "false",
        }
        try:
            data = self._get(f"{self.GAMMA_URL}/events", params=params)
            events = self._extract_list(data, "events")

            markets = []
            for event in events:
                # Chaque event contient ses marches
                event_markets = event.get("markets", [])
                if isinstance(event_markets, list):
                    for m in event_markets:
                        # Enrichir le marche avec les infos de l'event si manquantes
                        if not m.get("slug") and event.get("slug"):
                            m["slug"] = event["slug"]
                        markets.append(m)
                else:
                    # L'event lui-meme est un marche (events a 1 seul outcome)
                    markets.append(event)

            return markets[:limit]
        except Exception as e:
            print(f"[debug] /events fallback failed: {e}", file=sys.stderr)
            return []

    def get_events(self, limit=50, active=True, closed=False):
        """Fetch events from Gamma API."""
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "archived": "false",
        }
        data = self._get(f"{self.GAMMA_URL}/events", params=params)
        return self._extract_list(data, "events")

    def get_market(self, condition_id):
        """Fetch a single market by condition ID."""
        data = self._get(f"{self.GAMMA_URL}/markets/{condition_id}")
        # Peut retourner un seul objet ou une liste
        if isinstance(data, list):
            return data[0] if data else None
        return data

    def get_market_by_slug(self, slug):
        """
        Fetch a single market by slug.
        Gere les marches simples ET les events (groupes de marches).
        Pour les events, retourne le marche avec le plus de volume.

        Retourne (market_dict, all_sub_markets_or_None).
        """
        # Essai 1 : requete par slug exact sur /markets
        try:
            data = self._get(f"{self.GAMMA_URL}/markets", params={"slug": slug})
            markets = self._extract_list(data, "markets")
            if markets:
                for m in markets:
                    if m.get("slug") == slug:
                        return m, None
                return markets[0], None
        except Exception:
            pass

        # Essai 2 : requete par slug via /events (events multi-marches)
        try:
            data = self._get(f"{self.GAMMA_URL}/events", params={"slug": slug})
            events = self._extract_list(data, "events")
            for event in events:
                event_markets = event.get("markets", [])
                if isinstance(event_markets, list) and event_markets:
                    # Chercher un match exact de slug dans les sous-marches
                    for m in event_markets:
                        if m.get("slug") == slug:
                            return m, event_markets if len(event_markets) > 1 else None

                    # Pas de match exact -> c'est un event, retourner le plus actif
                    best = self._pick_best_market(event_markets)
                    return best, event_markets

                # L'event lui-meme est le marche
                if event.get("slug") == slug:
                    return event, None
        except Exception:
            pass

        # Essai 3 : recherche texte comme fallback
        results = self.search_markets(slug, limit=10)
        for m in results:
            if m.get("slug") == slug:
                return m, None
        return (results[0] if results else None), None

    @staticmethod
    def _pick_best_market(markets):
        """Retourne le marche avec le plus de volume parmi une liste."""
        best = None
        best_vol = -1
        for m in markets:
            vol = float(m.get("volume", 0) or m.get("volume24hr", 0) or 0)
            # Aussi prendre en compte les prix (ignorer les marches a 0%)
            yp, _, _, _ = PolymarketClient.extract_prices(m)
            if yp is not None and yp > 0.01 and yp < 0.99:
                vol += 10000  # Bonus pour les marches avec de vrais prix
            if vol > best_vol:
                best_vol = vol
                best = m
        return best or markets[0]

    def search_markets(self, query, limit=20):
        """Search markets by keyword."""
        params = {"query": query, "limit": limit}
        try:
            data = self._get(f"{self.GAMMA_URL}/markets", params=params)
            return self._extract_list(data, "markets")
        except Exception:
            # Fallback : chercher via events
            try:
                params_ev = {"tag": query, "limit": limit, "active": "true"}
                data = self._get(f"{self.GAMMA_URL}/events", params=params_ev)
                events = self._extract_list(data, "events")
                markets = []
                for event in events:
                    event_markets = event.get("markets", [])
                    if isinstance(event_markets, list):
                        markets.extend(event_markets)
                    else:
                        markets.append(event)
                return markets[:limit]
            except Exception:
                return []

    # -- CLOB API (order book / prices) --

    def get_price(self, token_id):
        """Get current price for a token."""
        return self._get(f"{self.CLOB_URL}/price", params={"token_id": token_id})

    def get_orderbook(self, token_id):
        """Get order book for a token."""
        return self._get(f"{self.CLOB_URL}/book", params={"token_id": token_id})

    def get_midpoint(self, token_id):
        """Get midpoint price for a token."""
        return self._get(f"{self.CLOB_URL}/midpoint", params={"token_id": token_id})

    # -- Utility --

    @staticmethod
    def extract_prices(market):
        """
        Extrait yes_price, no_price et token IDs depuis un objet marche.
        Gere les differents formats de l'API Gamma :
        - Format tokens: [{"outcome":"Yes","price":"0.65","token_id":"xxx"}, ...]
        - Format outcomePrices: "0.65,0.35" ou ["0.65","0.35"]
        - Format clobTokenIds: "id1,id2" ou ["id1","id2"]
        Retourne (yes_price, no_price, yes_token_id, no_token_id) ou (None, None, "", "")
        """
        yes_price = None
        no_price = None
        yes_token_id = ""
        no_token_id = ""

        # Methode 1 : tokens array (format classique)
        tokens = market.get("tokens", [])
        if isinstance(tokens, list) and len(tokens) >= 2:
            try:
                yes_price = float(tokens[0].get("price", 0))
                no_price = float(tokens[1].get("price", 0))
                yes_token_id = tokens[0].get("token_id", "")
                no_token_id = tokens[1].get("token_id", "")
                if yes_price > 0 or no_price > 0:
                    return yes_price, no_price, yes_token_id, no_token_id
            except (ValueError, TypeError, AttributeError):
                pass

        # Methode 2 : outcomePrices string ou list (format events)
        outcome_prices = market.get("outcomePrices", "")
        if outcome_prices:
            try:
                if isinstance(outcome_prices, str):
                    parts = outcome_prices.replace("[", "").replace("]", "").replace('"', '').split(",")
                elif isinstance(outcome_prices, list):
                    parts = outcome_prices
                else:
                    parts = []

                if len(parts) >= 2:
                    yes_price = float(str(parts[0]).strip())
                    no_price = float(str(parts[1]).strip())
            except (ValueError, TypeError):
                pass

        # Methode 3 : bestBid / prix direct
        if yes_price is None:
            if market.get("bestBid") is not None:
                try:
                    yes_price = float(market["bestBid"])
                    no_price = 1.0 - yes_price
                except (ValueError, TypeError):
                    pass

        # Token IDs depuis clobTokenIds
        if not yes_token_id:
            clob_ids = market.get("clobTokenIds", "")
            if clob_ids:
                try:
                    if isinstance(clob_ids, str):
                        id_parts = clob_ids.replace("[", "").replace("]", "").replace('"', '').split(",")
                    elif isinstance(clob_ids, list):
                        id_parts = clob_ids
                    else:
                        id_parts = []
                    if len(id_parts) >= 2:
                        yes_token_id = str(id_parts[0]).strip()
                        no_token_id = str(id_parts[1]).strip()
                except (ValueError, TypeError):
                    pass

        if yes_price is not None and no_price is not None:
            return yes_price, no_price, yes_token_id, no_token_id

        return None, None, "", ""
