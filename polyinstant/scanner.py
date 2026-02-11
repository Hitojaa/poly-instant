"""Scanner d'opportunites sur Polymarket."""

from datetime import datetime
from .client import PolymarketClient


class OpportunityScanner:
    """Scan les marches Polymarket pour trouver des opportunites d'arbitrage."""

    def __init__(self, min_profit_pct=2.0):
        self.client = PolymarketClient()
        self.min_profit_pct = min_profit_pct

    def scan_arbitrage(self, limit=100):
        """
        Cherche les marches ou YES + NO < 1.0 (arbitrage possible).

        L'idee : si tu achetes YES a 0.45 et NO a 0.50, tu payes 0.95.
        Un des deux vaudra 1.0 a la fin -> profit de 0.05 (5.2% net).
        """
        markets = self.client.get_markets(limit=limit)
        opportunities = []

        for market in markets:
            opp = self._check_market(market)
            if opp:
                opportunities.append(opp)

        return sorted(opportunities, key=lambda x: x["profit_pct"], reverse=True)

    def scan_mispriced(self, limit=100):
        """
        Cherche les marches ou les prix semblent decales
        (ex: YES a 0.90 avec gros volume = quasi-certitude, bon entry).
        """
        markets = self.client.get_markets(limit=limit)
        mispriced = []

        for market in markets:
            try:
                yes_price, no_price, _, _ = PolymarketClient.extract_prices(market)
                if yes_price is None:
                    continue

                volume = float(market.get("volume24hr", 0) or market.get("volume", 0) or 0)
                liquidity = float(market.get("liquidity", 0) or 0)

                if volume > 1000 and (yes_price > 0.85 or yes_price < 0.15):
                    mispriced.append({
                        "question": market.get("question", "?"),
                        "slug": market.get("slug", ""),
                        "yes_price": yes_price,
                        "no_price": no_price,
                        "volume_24h": volume,
                        "liquidity": liquidity,
                        "conviction": "YES" if yes_price > 0.5 else "NO",
                        "conviction_pct": max(yes_price, no_price) * 100,
                        "end_date": market.get("endDate", market.get("end_date", "")),
                    })
            except (ValueError, TypeError):
                continue

        return sorted(mispriced, key=lambda x: x["volume_24h"], reverse=True)

    def _check_market(self, market):
        """Verifie si un marche a une opportunite d'arbitrage."""
        try:
            yes_price, no_price, _, _ = PolymarketClient.extract_prices(market)
            if yes_price is None:
                return None

            if yes_price <= 0 or no_price <= 0:
                return None

            total_cost = yes_price + no_price
            profit_pct = (1.0 - total_cost) * 100

            if profit_pct >= self.min_profit_pct:
                return {
                    "question": market.get("question", "?"),
                    "slug": market.get("slug", ""),
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "total_cost": total_cost,
                    "profit_pct": profit_pct,
                    "volume_24h": float(market.get("volume24hr", 0) or market.get("volume", 0) or 0),
                    "liquidity": float(market.get("liquidity", 0) or 0),
                    "end_date": market.get("endDate", market.get("end_date", "")),
                }
        except (ValueError, TypeError):
            return None

        return None
