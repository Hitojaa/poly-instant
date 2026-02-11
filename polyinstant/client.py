"""Client pour les APIs Polymarket (Gamma + CLOB)."""

import requests
import time


class PolymarketClient:
    """Wrapper autour des APIs publiques Polymarket."""

    GAMMA_URL = "https://gamma-api.polymarket.com"
    CLOB_URL = "https://clob.polymarket.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "poly-instant/0.1",
        })

    def _get(self, url, params=None, retries=3):
        """GET with basic retry logic."""
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)

    # -- Gamma API (market metadata) --

    def get_markets(self, limit=50, active=True, closed=False):
        """Fetch active markets from Gamma API."""
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        return self._get(f"{self.GAMMA_URL}/markets", params=params)

    def get_market(self, condition_id):
        """Fetch a single market by condition ID."""
        return self._get(f"{self.GAMMA_URL}/markets/{condition_id}")

    def search_markets(self, query, limit=20):
        """Search markets by keyword."""
        params = {"query": query, "limit": limit}
        return self._get(f"{self.GAMMA_URL}/markets", params=params)

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
