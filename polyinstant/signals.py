"""Systeme de scoring de signaux - combine tous les indicateurs."""

from datetime import datetime
from .client import PolymarketClient
from .analytics import AdvancedAnalytics, MarketPredictor
from .collector import DataCollector


class SignalEngine:
    """
    Moteur de signaux qui combine toutes les analyses
    pour generer des alertes priorisees.

    Pipeline :
    1. Collecte de donnees (prix, orderbooks)
    2. Analyse avancee (Kelly, EV, Bayesian, momentum, whales)
    3. Scoring composite
    4. Filtrage et classement
    5. Notification
    """

    # Seuils de notification
    THRESHOLDS = {
        "min_signal_score": 20,       # Score minimum pour emettre un signal
        "min_arb_profit": 1.5,        # % minimum pour arbitrage
        "min_volume": 500,            # Volume 24h minimum
        "max_efficiency": 50,         # Score efficience max (on veut les inefficients)
        "whale_alert_pct": 30,        # % du volume en baleines pour alerter
        "momentum_rsi_overbought": 75,
        "momentum_rsi_oversold": 25,
    }

    def __init__(self, collector=None, notifier=None):
        self.client = PolymarketClient()
        self.analytics = AdvancedAnalytics(self.client)
        self.predictor = MarketPredictor(self.analytics)
        self.collector = collector or DataCollector()
        self.notifier = notifier  # TelegramNotifier ou None
        self.scan_count = 0
        self.alerts_sent = {"arb": 0, "signal": 0, "whale": 0, "momentum": 0, "efficiency": 0}

    def full_scan(self, limit=100):
        """
        Scan complet : collecte, analyse, scoring, notifications.

        Retourne un rapport avec tous les resultats.
        """
        self.scan_count += 1
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "scan_number": self.scan_count,
            "markets_scanned": 0,
            "arbitrages": [],
            "signals": [],
            "whale_alerts": [],
            "momentum_alerts": [],
            "efficiency_alerts": [],
        }

        # 1. Collecte
        count = self.collector.collect_snapshot(limit=limit)
        report["markets_scanned"] = count

        # 2. Recuperer les marches
        markets = self.client.get_markets(limit=limit)

        # 3. Analyse de chaque marche
        analyses = []
        for market in markets:
            slug = market.get("slug", "")
            question = market.get("question", "?")

            try:
                from .client import PolymarketClient as _PC
                yes_price, no_price, _, _ = _PC.extract_prices(market)
                if yes_price is None:
                    continue
                volume = float(market.get("volume24hr", 0) or market.get("volume", 0) or 0)
                liquidity = float(market.get("liquidity", 0) or 0)
            except (ValueError, TypeError):
                continue

            if volume < self.THRESHOLDS["min_volume"]:
                continue

            # --- Arbitrage check ---
            total_cost = yes_price + no_price
            if total_cost > 0 and total_cost < (1 - self.THRESHOLDS["min_arb_profit"] / 100):
                profit_pct = (1 - total_cost) * 100
                arb = {
                    "question": question,
                    "slug": slug,
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "total_cost": total_cost,
                    "profit_pct": profit_pct,
                    "volume_24h": volume,
                    "liquidity": liquidity,
                }
                report["arbitrages"].append(arb)
                if self.notifier:
                    self.notifier.alert_arbitrage(arb)
                    self.alerts_sent["arb"] += 1
                self.collector.log_event(slug, "ARBITRAGE", "HIGH",
                    f"Arbitrage {profit_pct:.1f}% detecte", arb)

            # --- Deep analysis (sur les marches avec du volume) ---
            if volume > 5000:
                analysis = self.analytics.deep_analysis(market)
                if analysis:
                    analyses.append(analysis)

                    # Whale alerts
                    for side in ["yes", "no"]:
                        whale_data = analysis.get("whales", {}).get(side)
                        if whale_data and whale_data.get("alert"):
                            report["whale_alerts"].append({
                                "slug": slug, "question": question,
                                "side": side.upper(), "data": whale_data,
                            })
                            if self.notifier:
                                self.notifier.alert_whale(slug, question, whale_data)
                                self.alerts_sent["whale"] += 1
                            self.collector.log_event(slug, "WHALE", "MEDIUM",
                                f"Baleine detectee ({side.upper()})", whale_data)

                    # Efficiency alerts
                    eff = analysis.get("efficiency", {})
                    if eff.get("opportunity"):
                        report["efficiency_alerts"].append({
                            "slug": slug, "question": question, "data": eff,
                        })
                        if self.notifier:
                            self.notifier.alert_efficiency(slug, question, eff)
                            self.alerts_sent["efficiency"] += 1

            # --- Momentum (si on a de l'historique) ---
            snapshots = self.collector.get_price_snapshots_for_momentum(slug, hours=6)
            if len(snapshots) >= 5:
                momentum = self.analytics.detect_momentum(snapshots)
                if momentum.get("signal") in ("STRONG_UP", "STRONG_DOWN"):
                    report["momentum_alerts"].append({
                        "slug": slug, "question": question, "data": momentum,
                    })
                    if self.notifier:
                        self.notifier.alert_momentum(slug, question, momentum)
                        self.alerts_sent["momentum"] += 1
                    self.collector.log_event(slug, "MOMENTUM", "MEDIUM",
                        f"Momentum {momentum['signal']}", momentum)

        # 4. Generer les signaux de trading
        if analyses:
            signals = self.predictor.generate_trade_signals(analyses)
            top_signals = [s for s in signals if s["score"] >= self.THRESHOLDS["min_signal_score"]]
            report["signals"] = top_signals[:20]

            # Notifier les top signaux
            if self.notifier:
                for sig in top_signals[:5]:
                    self.notifier.alert_signal(sig)
                    self.alerts_sent["signal"] += 1

        return report

    def get_stats(self):
        """Stats du moteur de signaux."""
        db_stats = self.collector.get_stats()
        return {
            "scans_count": self.scan_count,
            "alerts_sent": self.alerts_sent,
            "db_stats": db_stats,
            "markets_tracked": db_stats.get("market_meta", 0),
            "total_snapshots": db_stats.get("price_snapshots", 0),
        }

    def analyze_single(self, slug):
        """
        Analyse approfondie d'un seul marche avec tous les indicateurs.
        """
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

        analysis = self.analytics.deep_analysis(market)
        if not analysis:
            return None

        # Momentum si historique disponible
        actual_slug = market.get("slug", slug)
        snapshots = self.collector.get_price_snapshots_for_momentum(actual_slug, hours=24)
        momentum = None
        if len(snapshots) >= 3:
            momentum = self.analytics.detect_momentum(snapshots)

        # Prediction
        prediction = self.predictor.predict_direction(analysis)

        # Kelly avec la proba bayesienne
        bayesian = analysis.get("bayesian", {})
        posterior = bayesian.get("posterior", analysis["market"]["yes_price"])
        yes_price = analysis["market"]["yes_price"]

        kelly_adjusted = None
        if yes_price > 0:
            kelly_adjusted = self.analytics.kelly_criterion(posterior, 1.0 / yes_price)

        # EV avec la proba bayesienne
        ev_adjusted = self.analytics.expected_value(yes_price, posterior)

        return {
            **analysis,
            "momentum": momentum,
            "prediction": prediction,
            "kelly_adjusted": kelly_adjusted,
            "ev_adjusted": ev_adjusted,
            "history_points": len(snapshots),
        }
