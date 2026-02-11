"""Analyse detaillee d'un marche Polymarket."""

from .client import PolymarketClient


class MarketAnalyzer:
    """Analyse en profondeur un marche specifique."""

    def __init__(self):
        self.client = PolymarketClient()

    def analyze(self, slug):
        """Analyse complete d'un marche par son slug."""
        markets = self.client.search_markets(slug, limit=5)
        market = None
        for m in markets:
            if m.get("slug") == slug:
                market = m
                break
        if not market and markets:
            market = markets[0]
        if not market:
            return None

        tokens = market.get("tokens", [])
        if len(tokens) != 2:
            return {"error": "Marche non binaire, pas supporte pour l'instant"}

        yes_token = tokens[0]
        no_token = tokens[1]
        yes_price = float(yes_token.get("price", 0))
        no_price = float(no_token.get("price", 0))

        # Orderbook analysis
        yes_book = self._analyze_book(yes_token.get("token_id", ""))
        no_book = self._analyze_book(no_token.get("token_id", ""))

        return {
            "question": market.get("question", "?"),
            "slug": market.get("slug", ""),
            "description": market.get("description", ""),
            "end_date": market.get("endDate", ""),
            "yes_price": yes_price,
            "no_price": no_price,
            "spread": abs(1.0 - yes_price - no_price),
            "volume_24h": float(market.get("volume24hr", 0) or 0),
            "volume_total": float(market.get("volume", 0) or 0),
            "liquidity": float(market.get("liquidity", 0) or 0),
            "yes_orderbook": yes_book,
            "no_orderbook": no_book,
            "arb_opportunity": (yes_price + no_price) < 0.98,
            "arb_profit_pct": (1.0 - yes_price - no_price) * 100,
        }

    def _analyze_book(self, token_id):
        """Analyse l'order book d'un token."""
        if not token_id:
            return None
        try:
            book = self.client.get_orderbook(token_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            bid_depth = sum(float(b.get("size", 0)) for b in bids[:10])
            ask_depth = sum(float(a.get("size", 0)) for a in asks[:10])

            best_bid = float(bids[0]["price"]) if bids else 0
            best_ask = float(asks[0]["price"]) if asks else 0

            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid if best_bid and best_ask else 0,
                "bid_depth_10": bid_depth,
                "ask_depth_10": ask_depth,
                "num_bids": len(bids),
                "num_asks": len(asks),
            }
        except Exception:
            return None
