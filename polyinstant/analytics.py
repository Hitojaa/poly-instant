"""Moteur d'analyse avancee - mathematiques, statistiques, predictions."""

import math
import statistics
from datetime import datetime, timezone
from collections import defaultdict


class AdvancedAnalytics:
    """
    Analyse mathematique poussee des marches Polymarket.

    Inclut : Kelly Criterion, Expected Value, Bayesian updates,
    momentum detection, whale detection, volume profile analysis.
    """

    def __init__(self, client):
        self.client = client
        # Cache pour tracker les prix dans le temps (en memoire)
        self.price_history = defaultdict(list)  # slug -> [(timestamp, yes_price, no_price, volume)]

    # -------------------------------------------------------------------------
    # KELLY CRITERION - Taille optimale de mise
    # -------------------------------------------------------------------------

    def kelly_criterion(self, prob_win, odds_decimal):
        """
        Calcule la fraction optimale de bankroll a miser (Kelly Criterion).

        f* = (bp - q) / b
        ou b = odds - 1, p = prob de gain, q = 1 - p

        Retourne un dict avec kelly complet et demi-kelly (plus conservateur).
        """
        if odds_decimal <= 1 or prob_win <= 0 or prob_win >= 1:
            return {"full_kelly": 0, "half_kelly": 0, "edge": 0, "recommendation": "NO BET"}

        b = odds_decimal - 1
        p = prob_win
        q = 1 - p

        kelly = (b * p - q) / b
        edge = (b * p - q) * 100  # edge en pourcentage

        if kelly <= 0:
            return {
                "full_kelly": 0,
                "half_kelly": 0,
                "quarter_kelly": 0,
                "edge": edge,
                "recommendation": "NO BET - Pas d'edge",
            }

        return {
            "full_kelly": round(kelly * 100, 2),
            "half_kelly": round(kelly * 50, 2),
            "quarter_kelly": round(kelly * 25, 2),
            "edge": round(edge, 2),
            "recommendation": self._kelly_recommendation(kelly),
        }

    def _kelly_recommendation(self, kelly):
        if kelly > 0.25:
            return "FORTE OPPORTUNITE - Utiliser quarter-kelly max (risque eleve)"
        elif kelly > 0.10:
            return "BONNE OPPORTUNITE - Half-kelly recommande"
        elif kelly > 0.05:
            return "OPPORTUNITE MODEREE - Quarter-kelly recommande"
        else:
            return "FAIBLE EDGE - Petite mise ou passer"

    # -------------------------------------------------------------------------
    # EXPECTED VALUE - Valeur esperee d'un trade
    # -------------------------------------------------------------------------

    def expected_value(self, buy_price, estimated_prob, fees_pct=2.0):
        """
        Calcule l'EV (Expected Value) d'un achat.

        EV = (prob_win * payout_win) + (prob_lose * payout_lose) - cost

        Sur Polymarket : payout_win = 1.0, payout_lose = 0.0
        """
        fee_multiplier = 1 - (fees_pct / 100)
        cost = buy_price

        # Si on gagne : on recoit 1.0 moins les frais
        payout_win = 1.0 * fee_multiplier
        # Si on perd : on perd tout
        payout_lose = 0.0

        ev = (estimated_prob * payout_win) + ((1 - estimated_prob) * payout_lose) - cost
        ev_pct = (ev / cost) * 100 if cost > 0 else 0

        # ROI potentiel
        roi = ((payout_win - cost) / cost) * 100 if cost > 0 else 0

        return {
            "ev_absolute": round(ev, 4),
            "ev_percentage": round(ev_pct, 2),
            "cost": cost,
            "potential_roi": round(roi, 2),
            "breakeven_prob": round(cost / payout_win, 4),
            "edge_over_market": round((estimated_prob - cost / payout_win) * 100, 2),
            "verdict": "EV+" if ev > 0 else "EV-",
        }

    # -------------------------------------------------------------------------
    # BAYESIAN PROBABILITY UPDATE
    # -------------------------------------------------------------------------

    def bayesian_update(self, prior_prob, volume_24h, liquidity, price_momentum, book_imbalance):
        """
        Met a jour la probabilite estimee en utilisant l'inference bayesienne.

        Prend en compte :
        - Volume recent (plus de volume = plus de confiance dans le prix)
        - Liquidite (plus de liquidite = moins de manipulation possible)
        - Momentum du prix (tendance haussiere/baissiere)
        - Desequilibre du carnet d'ordres (pression achat vs vente)

        Retourne une probabilite ajustee.
        """
        adjusted = prior_prob
        confidence = 0.5  # confiance de base

        # Factor 1: Volume - Plus de volume = plus le prix est fiable
        if volume_24h > 100000:
            confidence += 0.2
        elif volume_24h > 10000:
            confidence += 0.1
        elif volume_24h < 1000:
            confidence -= 0.15

        # Factor 2: Liquidite - Marches liquides = moins manipulables
        if liquidity > 50000:
            confidence += 0.1
        elif liquidity < 5000:
            confidence -= 0.1

        # Factor 3: Momentum - Si le prix monte, ajuster a la hausse
        if abs(price_momentum) > 0.05:
            momentum_adjustment = price_momentum * 0.3  # 30% du momentum
            adjusted += momentum_adjustment

        # Factor 4: Book imbalance - Si beaucoup plus de bids que d'asks
        if abs(book_imbalance) > 0.2:
            book_adjustment = book_imbalance * 0.1
            adjusted += book_adjustment

        # Clamp entre 0.01 et 0.99
        adjusted = max(0.01, min(0.99, adjusted))
        confidence = max(0.1, min(1.0, confidence))

        return {
            "prior": round(prior_prob, 4),
            "posterior": round(adjusted, 4),
            "confidence": round(confidence, 2),
            "adjustment": round(adjusted - prior_prob, 4),
            "confidence_label": self._confidence_label(confidence),
        }

    def _confidence_label(self, conf):
        if conf >= 0.8:
            return "TRES HAUTE"
        elif conf >= 0.6:
            return "HAUTE"
        elif conf >= 0.4:
            return "MOYENNE"
        elif conf >= 0.2:
            return "BASSE"
        return "TRES BASSE"

    # -------------------------------------------------------------------------
    # MOMENTUM & TREND DETECTION
    # -------------------------------------------------------------------------

    def detect_momentum(self, price_snapshots):
        """
        Detecte le momentum a partir d'une serie de snapshots de prix.

        price_snapshots = [(timestamp, price), ...]

        Calcule :
        - Tendance lineaire (regression simple)
        - Volatilite (ecart-type des rendements)
        - RSI simplifie
        - Acceleration (momentum du momentum)
        """
        if len(price_snapshots) < 3:
            return {
                "trend": "INSUFFICIENT_DATA",
                "momentum": 0,
                "volatility": 0,
                "rsi": 50,
                "acceleration": 0,
                "signal": "NEUTRAL",
            }

        prices = [p[1] for p in price_snapshots]

        # Rendements (returns)
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])

        if not returns:
            return {"trend": "FLAT", "momentum": 0, "volatility": 0, "rsi": 50, "acceleration": 0, "signal": "NEUTRAL"}

        # Momentum = rendement moyen
        momentum = statistics.mean(returns)

        # Volatilite = ecart-type des rendements
        volatility = statistics.stdev(returns) if len(returns) > 1 else 0

        # RSI simplifie
        gains = [r for r in returns if r > 0]
        losses = [-r for r in returns if r < 0]
        avg_gain = statistics.mean(gains) if gains else 0
        avg_loss = statistics.mean(losses) if losses else 0.0001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))

        # Acceleration (diff de momentum recente vs ancienne)
        mid = len(returns) // 2
        if mid > 0 and len(returns) > mid:
            old_momentum = statistics.mean(returns[:mid])
            new_momentum = statistics.mean(returns[mid:])
            acceleration = new_momentum - old_momentum
        else:
            acceleration = 0

        # Tendance lineaire (pente normalisee)
        n = len(prices)
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(prices)
        numerator = sum((i - x_mean) * (prices[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator > 0 else 0
        normalized_slope = slope / y_mean if y_mean > 0 else 0

        # Signal
        if rsi > 70 and momentum > 0.01:
            signal = "STRONG_UP"
            trend = "HAUSSIER FORT"
        elif rsi > 55 and momentum > 0:
            signal = "UP"
            trend = "HAUSSIER"
        elif rsi < 30 and momentum < -0.01:
            signal = "STRONG_DOWN"
            trend = "BAISSIER FORT"
        elif rsi < 45 and momentum < 0:
            signal = "DOWN"
            trend = "BAISSIER"
        else:
            signal = "NEUTRAL"
            trend = "NEUTRE"

        return {
            "trend": trend,
            "momentum": round(momentum * 100, 4),
            "volatility": round(volatility * 100, 4),
            "rsi": round(rsi, 1),
            "acceleration": round(acceleration * 100, 4),
            "slope": round(normalized_slope * 100, 4),
            "signal": signal,
            "last_price": prices[-1],
            "price_change_pct": round((prices[-1] - prices[0]) / prices[0] * 100, 2) if prices[0] > 0 else 0,
        }

    # -------------------------------------------------------------------------
    # WHALE DETECTION - Detection des gros joueurs
    # -------------------------------------------------------------------------

    def detect_whales(self, orderbook):
        """
        Detecte les ordres de baleines (gros montants) dans le carnet d'ordres.

        Une baleine = un ordre dont la taille est > 3x la taille mediane.
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        all_sizes = []
        for order in bids + asks:
            try:
                all_sizes.append(float(order.get("size", 0)))
            except (ValueError, TypeError):
                continue

        if len(all_sizes) < 5:
            return {
                "whale_orders": [],
                "whale_count": 0,
                "whale_volume": 0,
                "total_volume": 0,
                "whale_pct": 0,
                "whale_bias": "NEUTRAL",
                "alert": False,
            }

        median_size = statistics.median(all_sizes)
        whale_threshold = median_size * 3
        total_volume = sum(all_sizes)

        whale_bids = []
        whale_asks = []

        for bid in bids:
            size = float(bid.get("size", 0))
            if size >= whale_threshold:
                whale_bids.append({
                    "side": "BID",
                    "price": float(bid.get("price", 0)),
                    "size": size,
                    "ratio_vs_median": round(size / median_size, 1) if median_size > 0 else 0,
                })

        for ask in asks:
            size = float(ask.get("size", 0))
            if size >= whale_threshold:
                whale_asks.append({
                    "side": "ASK",
                    "price": float(ask.get("price", 0)),
                    "size": size,
                    "ratio_vs_median": round(size / median_size, 1) if median_size > 0 else 0,
                })

        whale_bid_vol = sum(w["size"] for w in whale_bids)
        whale_ask_vol = sum(w["size"] for w in whale_asks)
        whale_total = whale_bid_vol + whale_ask_vol

        # Biais : les baleines achetent ou vendent ?
        if whale_bid_vol > whale_ask_vol * 1.5:
            bias = "BULLISH - Baleines a l'achat"
        elif whale_ask_vol > whale_bid_vol * 1.5:
            bias = "BEARISH - Baleines a la vente"
        else:
            bias = "NEUTRAL"

        all_whales = sorted(whale_bids + whale_asks, key=lambda x: x["size"], reverse=True)

        return {
            "whale_orders": all_whales[:10],  # top 10
            "whale_count": len(all_whales),
            "whale_volume": round(whale_total, 2),
            "total_volume": round(total_volume, 2),
            "whale_pct": round(whale_total / total_volume * 100, 1) if total_volume > 0 else 0,
            "whale_bias": bias,
            "whale_threshold": round(whale_threshold, 2),
            "median_order_size": round(median_size, 2),
            "alert": whale_total > total_volume * 0.3,  # Alerte si >30% du volume = baleines
        }

    # -------------------------------------------------------------------------
    # VOLUME PROFILE ANALYSIS
    # -------------------------------------------------------------------------

    def volume_profile(self, orderbook, num_levels=20):
        """
        Analyse le profil de volume dans le carnet d'ordres.

        Identifie :
        - Zones de support (clusters de bids)
        - Zones de resistance (clusters d'asks)
        - Point of Control (niveau de prix avec le plus de volume)
        - Value Area (zone ou 70% du volume est concentre)
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        # Construire le profil
        price_volume = defaultdict(float)
        bid_volume = defaultdict(float)
        ask_volume = defaultdict(float)

        for bid in bids:
            price = round(float(bid.get("price", 0)), 2)
            size = float(bid.get("size", 0))
            price_volume[price] += size
            bid_volume[price] += size

        for ask in asks:
            price = round(float(ask.get("price", 0)), 2)
            size = float(ask.get("size", 0))
            price_volume[price] += size
            ask_volume[price] += size

        if not price_volume:
            return {
                "poc_price": 0, "poc_volume": 0,
                "support_zones": [], "resistance_zones": [],
                "bid_total": 0, "ask_total": 0,
                "buy_sell_ratio": 0,
            }

        # Point of Control = prix avec le plus de volume
        poc_price = max(price_volume, key=price_volume.get)
        poc_volume = price_volume[poc_price]

        # Total volumes
        total_bid = sum(bid_volume.values())
        total_ask = sum(ask_volume.values())

        # Buy/Sell ratio
        buy_sell_ratio = total_bid / total_ask if total_ask > 0 else float('inf')

        # Support zones (top bid clusters)
        supports = sorted(bid_volume.items(), key=lambda x: x[1], reverse=True)[:5]
        resistances = sorted(ask_volume.items(), key=lambda x: x[1], reverse=True)[:5]

        # Value Area (70% du volume)
        sorted_levels = sorted(price_volume.items(), key=lambda x: x[1], reverse=True)
        total_vol = sum(v for _, v in sorted_levels)
        cumulative = 0
        value_area = []
        for price, vol in sorted_levels:
            cumulative += vol
            value_area.append(price)
            if cumulative >= total_vol * 0.7:
                break

        va_high = max(value_area) if value_area else 0
        va_low = min(value_area) if value_area else 0

        return {
            "poc_price": poc_price,
            "poc_volume": round(poc_volume, 2),
            "value_area_high": va_high,
            "value_area_low": va_low,
            "support_zones": [{"price": p, "volume": round(v, 2)} for p, v in supports],
            "resistance_zones": [{"price": p, "volume": round(v, 2)} for p, v in resistances],
            "bid_total": round(total_bid, 2),
            "ask_total": round(total_ask, 2),
            "buy_sell_ratio": round(buy_sell_ratio, 3),
            "pressure": "ACHAT" if buy_sell_ratio > 1.2 else ("VENTE" if buy_sell_ratio < 0.8 else "EQUILIBRE"),
        }

    # -------------------------------------------------------------------------
    # MARKET EFFICIENCY SCORE
    # -------------------------------------------------------------------------

    def efficiency_score(self, yes_price, no_price, spread, volume_24h, liquidity, book_spread_yes, book_spread_no):
        """
        Calcule un score d'efficience du marche (0-100).

        Un marche efficient = difficile a battre.
        Un marche inefficient = opportunites potentielles.

        Facteurs :
        - Deviation de YES + NO par rapport a 1.0
        - Spread bid-ask
        - Volume (plus de volume = plus efficient)
        - Liquidite
        """
        score = 100  # On part de 100 (parfaitement efficient) et on deduit

        # 1. Deviation des prix (-30 max)
        price_deviation = abs(1.0 - yes_price - no_price)
        if price_deviation > 0.05:
            score -= 30
        elif price_deviation > 0.02:
            score -= 15
        elif price_deviation > 0.01:
            score -= 5

        # 2. Spread bid-ask (-25 max)
        avg_spread = (book_spread_yes + book_spread_no) / 2
        if avg_spread > 0.10:
            score -= 25
        elif avg_spread > 0.05:
            score -= 15
        elif avg_spread > 0.02:
            score -= 8

        # 3. Volume (-25 max)
        if volume_24h < 1000:
            score -= 25
        elif volume_24h < 10000:
            score -= 15
        elif volume_24h < 50000:
            score -= 5

        # 4. Liquidite (-20 max)
        if liquidity < 5000:
            score -= 20
        elif liquidity < 20000:
            score -= 10
        elif liquidity < 50000:
            score -= 5

        score = max(0, score)

        return {
            "score": score,
            "label": self._efficiency_label(score),
            "detail": {
                "price_deviation": round(price_deviation, 4),
                "avg_book_spread": round(avg_spread, 4),
                "volume_24h": volume_24h,
                "liquidity": liquidity,
            },
            "opportunity": score < 50,
        }

    def _efficiency_label(self, score):
        if score >= 80:
            return "TRES EFFICIENT - Peu d'opportunites"
        elif score >= 60:
            return "EFFICIENT - Quelques inefficiences"
        elif score >= 40:
            return "MODEREMENT EFFICIENT - Opportunites possibles"
        elif score >= 20:
            return "INEFFICIENT - Bonnes opportunites"
        return "TRES INEFFICIENT - Opportunites majeures"

    # -------------------------------------------------------------------------
    # FULL MARKET DEEP ANALYSIS
    # -------------------------------------------------------------------------

    def deep_analysis(self, market):
        """
        Analyse complete et approfondie d'un marche.
        Combine toutes les metriques en un rapport unifie.
        """
        from .client import PolymarketClient as _PC
        yes_price, no_price, yes_token_id, no_token_id = _PC.extract_prices(market)
        if yes_price is None:
            return None

        volume_24h = float(market.get("volume24hr", 0) or market.get("volume", 0) or 0)
        liquidity = float(market.get("liquidity", 0) or 0)

        # Orderbooks
        yes_book = self._safe_get_book(yes_token_id)
        no_book = self._safe_get_book(no_token_id)

        # Book spreads
        yes_spread = self._book_spread(yes_book)
        no_spread = self._book_spread(no_book)

        # Kelly (on utilise le prix du marche comme proxy de prob)
        kelly_yes = self.kelly_criterion(yes_price, 1.0 / yes_price if yes_price > 0 else 0)
        kelly_no = self.kelly_criterion(no_price, 1.0 / no_price if no_price > 0 else 0)

        # EV
        ev_yes = self.expected_value(yes_price, yes_price)  # EV neutre (le marche est efficient)
        ev_no = self.expected_value(no_price, no_price)

        # Volume profile
        vp_yes = self.volume_profile(yes_book) if yes_book else None
        vp_no = self.volume_profile(no_book) if no_book else None

        # Whale detection
        whales_yes = self.detect_whales(yes_book) if yes_book else None
        whales_no = self.detect_whales(no_book) if no_book else None

        # Efficiency
        efficiency = self.efficiency_score(
            yes_price, no_price,
            abs(1.0 - yes_price - no_price),
            volume_24h, liquidity,
            yes_spread, no_spread,
        )

        # Book imbalance pour Bayesian
        book_imbalance = 0
        if vp_yes:
            ratio = vp_yes["buy_sell_ratio"]
            book_imbalance = (ratio - 1) / (ratio + 1) if ratio > 0 else 0

        # Bayesian update
        bayesian = self.bayesian_update(
            prior_prob=yes_price,
            volume_24h=volume_24h,
            liquidity=liquidity,
            price_momentum=0,  # Pas de momentum sans historique
            book_imbalance=book_imbalance,
        )

        return {
            "market": {
                "question": market.get("question", "?"),
                "slug": market.get("slug", ""),
                "end_date": market.get("endDate", ""),
                "yes_price": yes_price,
                "no_price": no_price,
                "total_cost": yes_price + no_price,
                "spread": abs(1.0 - yes_price - no_price),
                "volume_24h": volume_24h,
                "volume_total": float(market.get("volume", 0) or 0),
                "liquidity": liquidity,
            },
            "kelly": {"yes": kelly_yes, "no": kelly_no},
            "ev": {"yes": ev_yes, "no": ev_no},
            "bayesian": bayesian,
            "efficiency": efficiency,
            "volume_profile": {"yes": vp_yes, "no": vp_no},
            "whales": {"yes": whales_yes, "no": whales_no},
            "book_spreads": {"yes": yes_spread, "no": no_spread},
        }

    def _safe_get_book(self, token_id):
        if not token_id:
            return {"bids": [], "asks": []}
        try:
            return self.client.get_orderbook(token_id)
        except Exception:
            return {"bids": [], "asks": []}

    def _book_spread(self, book):
        if not book:
            return 0
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if bids and asks:
            return float(asks[0].get("price", 0)) - float(bids[0].get("price", 0))
        return 0


class MarketPredictor:
    """
    Predicteur de marche base sur des heuristiques et patterns.

    Note : ce n'est PAS du machine learning, mais des heuristiques
    basees sur la microstructure du marche.
    """

    def __init__(self, analytics):
        self.analytics = analytics

    def predict_direction(self, market_analysis):
        """
        Predit la direction probable du marche en combinant les signaux.

        Retourne un score de -100 (tres baissier) a +100 (tres haussier)
        avec un niveau de confiance.
        """
        signals = []

        # Signal 1: Biais des baleines
        whales_yes = market_analysis.get("whales", {}).get("yes")
        if whales_yes and whales_yes.get("whale_bias"):
            bias = whales_yes["whale_bias"]
            if "BULLISH" in bias:
                signals.append(("whale_bias", 30, 0.7))
            elif "BEARISH" in bias:
                signals.append(("whale_bias", -30, 0.7))
            else:
                signals.append(("whale_bias", 0, 0.3))

        # Signal 2: Volume profile pressure
        vp_yes = market_analysis.get("volume_profile", {}).get("yes")
        if vp_yes:
            pressure = vp_yes.get("pressure", "")
            if pressure == "ACHAT":
                signals.append(("volume_pressure", 25, 0.6))
            elif pressure == "VENTE":
                signals.append(("volume_pressure", -25, 0.6))
            else:
                signals.append(("volume_pressure", 0, 0.3))

        # Signal 3: Bayesian adjustment
        bayesian = market_analysis.get("bayesian", {})
        adjustment = bayesian.get("adjustment", 0)
        if abs(adjustment) > 0.01:
            direction = 20 if adjustment > 0 else -20
            signals.append(("bayesian", direction, bayesian.get("confidence", 0.5)))

        # Signal 4: Efficiency (marche inefficient = plus de potentiel de mouvement)
        efficiency = market_analysis.get("efficiency", {})
        if efficiency.get("opportunity"):
            # Marche inefficient, direction basee sur le bias global
            total_bias = sum(s[1] * s[2] for s in signals)
            signals.append(("inefficiency", 15 if total_bias > 0 else -15, 0.4))

        if not signals:
            return {
                "direction_score": 0,
                "confidence": 0,
                "prediction": "NEUTRE",
                "signals": [],
            }

        # Score pondere
        weighted_sum = sum(score * confidence for _, score, confidence in signals)
        total_confidence = sum(confidence for _, _, confidence in signals)
        direction_score = weighted_sum / total_confidence if total_confidence > 0 else 0
        avg_confidence = total_confidence / len(signals) if signals else 0

        # Prediction
        if direction_score > 20:
            prediction = "HAUSSIER"
        elif direction_score > 40:
            prediction = "TRES HAUSSIER"
        elif direction_score < -20:
            prediction = "BAISSIER"
        elif direction_score < -40:
            prediction = "TRES BAISSIER"
        else:
            prediction = "NEUTRE"

        return {
            "direction_score": round(direction_score, 1),
            "confidence": round(avg_confidence, 2),
            "prediction": prediction,
            "signals": [
                {"name": name, "score": score, "confidence": conf}
                for name, score, conf in signals
            ],
        }

    def generate_trade_signals(self, markets_analyses):
        """
        Genere des signaux de trade classes par priorite
        a partir de l'analyse de plusieurs marches.
        """
        signals = []

        for analysis in markets_analyses:
            if not analysis:
                continue

            market_info = analysis.get("market", {})
            efficiency = analysis.get("efficiency", {})
            prediction = self.predict_direction(analysis)

            # Score composite
            score = 0
            reasons = []

            # Arbitrage pur
            spread = market_info.get("spread", 0)
            total_cost = market_info.get("total_cost", 1)
            if total_cost < 0.98:
                arb_profit = (1 - total_cost) * 100
                score += arb_profit * 10  # L'arbitrage est roi
                reasons.append(f"Arbitrage: {arb_profit:.1f}% profit")

            # Inefficience
            if efficiency.get("opportunity"):
                score += 30
                reasons.append(f"Marche inefficient (score: {efficiency.get('score', 0)})")

            # Direction forte
            if abs(prediction["direction_score"]) > 20 and prediction["confidence"] > 0.5:
                score += abs(prediction["direction_score"])
                reasons.append(f"Signal directionnel: {prediction['prediction']}")

            # Volume
            vol = market_info.get("volume_24h", 0)
            if vol > 50000:
                score += 10
                reasons.append(f"Fort volume: ${vol:,.0f}")

            if score > 0:
                signals.append({
                    "market": market_info.get("question", "?"),
                    "slug": market_info.get("slug", ""),
                    "score": round(score, 1),
                    "direction": prediction["prediction"],
                    "direction_score": prediction["direction_score"],
                    "confidence": prediction["confidence"],
                    "yes_price": market_info.get("yes_price", 0),
                    "no_price": market_info.get("no_price", 0),
                    "volume_24h": vol,
                    "reasons": reasons,
                })

        return sorted(signals, key=lambda x: x["score"], reverse=True)
