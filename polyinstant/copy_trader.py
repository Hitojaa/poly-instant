"""Copy trading : suivi en temps reel des smart wallets et alertes."""

import time
import json
from datetime import datetime, timedelta
from collections import defaultdict

from .blockchain import PolygonClient
from .client import PolymarketClient
from .wallet_tracker import WalletTracker
from .smart_money import SmartMoneyDetector


class CopyTrader:
    """
    Moniteur de copy-trading en temps reel.

    Surveille les wallets de la watchlist et genere des alertes
    quand un smart wallet ouvre une position.

    Modes :
    1. MONITOR : Surveille et alerte (Telegram)
    2. ANALYZE : Analyse les positions actuelles des smart wallets
    3. SIGNAL : Genere des signaux agreges (quand plusieurs smart wallets
       achetent le meme outcome)

    Configuration :
    - min_wallets_consensus : nombre minimum de smart wallets sur le meme
      outcome pour generer un signal fort
    - min_score_to_copy : score minimum d'un wallet pour le copier
    - signal_cooldown : temps entre 2 signaux pour le meme marche
    """

    def __init__(self, polygonscan_api_key="", notifier=None):
        self.poly_client = PolymarketClient()
        self.chain = PolygonClient(polygonscan_api_key)
        self.tracker = WalletTracker(polygonscan_api_key)
        self.detector = SmartMoneyDetector(polygonscan_api_key)
        self.notifier = notifier

        # Config
        self.min_wallets_consensus = 3
        self.min_score_to_copy = 65
        self.signal_cooldown = 600  # 10 min

        # State
        self._last_signals = {}  # market_slug -> timestamp
        self._known_positions = {}  # wallet -> {slug -> outcome}
        self.signals_generated = 0

    # =========================================================================
    # POSITION TRACKING
    # =========================================================================

    def scan_smart_positions(self, limit=50):
        """
        Scanne les positions actuelles des smart wallets sur les marches actifs.

        Pour chaque marche actif :
        - Quels smart wallets ont des positions ?
        - Quel est le consensus (YES ou NO) ?
        - Quel est le volume smart money ?
        """
        smart_wallets = self.tracker.get_smart_wallets(min_score=self.min_score_to_copy)
        if not smart_wallets:
            return {"positions": [], "consensus_signals": [], "smart_wallets_count": 0}

        markets = self.poly_client.get_markets(limit=limit)
        positions = []
        consensus_signals = []

        for market in markets:
            slug = market.get("slug", "")
            question = market.get("question", "?")

            from .client import PolymarketClient as _PC
            yes_price, no_price, _, _ = _PC.extract_prices(market)
            if yes_price is None:
                continue

            # Checker le flow smart money pour ce marche
            flow = self.detector.smart_money_flow(slug)
            if not flow or not flow["smart_money"]["trades"]:
                continue

            sm = flow["smart_money"]
            positions.append({
                "slug": slug,
                "question": question,
                "yes_price": yes_price,
                "no_price": no_price,
                "smart_wallets": sm["wallets"],
                "smart_trades": sm["trades"],
                "smart_bias": sm["bias"],
                "smart_conviction": sm["conviction"],
                "smart_yes_vol": sm["yes_volume"],
                "smart_no_vol": sm["no_volume"],
                "top_traders": flow.get("top_smart_traders", [])[:3],
                "signal": flow.get("signal", {}),
            })

            # Generer un signal de consensus si assez de smart wallets sont d'accord
            if sm["wallets"] >= self.min_wallets_consensus and sm["conviction"] > 60:
                signal = {
                    "type": "SMART_MONEY_CONSENSUS",
                    "slug": slug,
                    "question": question,
                    "direction": sm["bias"],
                    "smart_wallets": sm["wallets"],
                    "conviction": sm["conviction"],
                    "volume": sm["total_volume"],
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "timestamp": datetime.utcnow().isoformat(),
                }

                if self._should_signal(slug):
                    consensus_signals.append(signal)
                    self.signals_generated += 1

                    if self.notifier:
                        self._notify_consensus(signal)

        # Trier par conviction
        positions.sort(key=lambda x: x["smart_conviction"], reverse=True)
        consensus_signals.sort(key=lambda x: x["conviction"], reverse=True)

        return {
            "positions": positions,
            "consensus_signals": consensus_signals,
            "smart_wallets_count": len(smart_wallets),
        }

    def _should_signal(self, slug):
        """Verifie le cooldown entre les signaux."""
        now = time.time()
        last = self._last_signals.get(slug, 0)
        if now - last < self.signal_cooldown:
            return False
        self._last_signals[slug] = now
        return True

    # =========================================================================
    # WALLET MONITORING - Detect new trades from tracked wallets
    # =========================================================================

    def monitor_watchlist(self):
        """
        Verifie les trades recents des wallets de la watchlist.

        Compare les positions actuelles avec les positions connues
        pour detecter les nouveaux trades.
        """
        watchlist = self.tracker.get_watchlist()
        if not watchlist:
            return {"new_trades": [], "watchlist_size": 0}

        new_trades = []
        for wallet in watchlist:
            address = wallet["address"]
            try:
                trades = self.chain.get_wallet_trades(address, limit=20)
            except Exception:
                continue

            if not trades:
                continue

            # Comparer avec les trades connus
            known = self._known_positions.get(address, set())
            for trade in trades:
                trade_id = f"{trade.get('id', '')}_{trade.get('timestamp', '')}"
                if trade_id in known:
                    continue

                # Nouveau trade detecte
                known.add(trade_id)
                outcome = trade.get("side", trade.get("outcome", ""))
                asset_id = trade.get("asset_id", trade.get("token_id", ""))

                new_trade = {
                    "wallet": address,
                    "wallet_score": wallet.get("score", 0),
                    "wallet_tier": wallet.get("tier", ""),
                    "wallet_win_rate": wallet.get("win_rate", 0),
                    "outcome": outcome,
                    "price": float(trade.get("price", 0)),
                    "size": float(trade.get("size", trade.get("amount", 0))),
                    "asset_id": asset_id,
                    "timestamp": trade.get("timestamp", ""),
                    "auto_copy": wallet.get("auto_copy", False),
                }
                new_trades.append(new_trade)

                # Notification
                if self.notifier and wallet.get("priority", 0) >= 2:
                    self._notify_wallet_trade(new_trade, wallet)

            self._known_positions[address] = known

        return {
            "new_trades": new_trades,
            "watchlist_size": len(watchlist),
        }

    # =========================================================================
    # CONTINUOUS MONITORING LOOP
    # =========================================================================

    def run_monitor(self, interval=60, index_interval=300):
        """
        Boucle de monitoring continu.

        Toutes les `interval` secondes :
        - Check les trades des wallets de la watchlist
        - Scan les positions smart money

        Toutes les `index_interval` secondes :
        - Re-indexe les marches resolus
        - Re-score les wallets
        """
        print(f"[copy-trader] Demarrage du monitoring")
        print(f"  Interval scan: {interval}s")
        print(f"  Interval indexation: {index_interval}s")

        if self.notifier:
            self.notifier.send(
                "<b>Copy Trader actif</b>\n\n"
                f"Monitoring toutes les {interval}s\n"
                f"Indexation toutes les {index_interval}s",
                parse_mode="HTML",
            )

        last_index = 0
        scan_count = 0

        while True:
            try:
                now = time.time()
                scan_count += 1

                # Indexation periodique
                if now - last_index >= index_interval:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Indexation des marches resolus...")
                    try:
                        idx_result = self.tracker.index_resolved_markets(limit=30)
                        print(f"  Indexe: {idx_result['trades_indexed']} trades, {idx_result['wallets_updated']} wallets")

                        if idx_result["trades_indexed"] > 0:
                            scored = self.tracker.score_all_wallets(min_trades=5)
                            print(f"  Score: {scored} wallets scores")
                    except Exception as e:
                        print(f"  [erreur indexation] {e}")

                    last_index = now

                # Monitoring watchlist
                watchlist_result = self.monitor_watchlist()
                if watchlist_result["new_trades"]:
                    print(f"\n  NOUVEAUX TRADES ({len(watchlist_result['new_trades'])})")
                    for nt in watchlist_result["new_trades"][:5]:
                        print(f"    {nt['wallet'][:10]}... | {nt['outcome']} @ {nt['price']:.3f} | size: {nt['size']:.0f}")

                # Scan positions smart money
                if scan_count % 5 == 0:  # Toutes les 5 scans
                    positions = self.scan_smart_positions(limit=50)
                    if positions["consensus_signals"]:
                        print(f"\n  CONSENSUS SMART MONEY ({len(positions['consensus_signals'])})")
                        for sig in positions["consensus_signals"][:3]:
                            print(f"    {sig['question'][:45]} | {sig['direction']} | "
                                  f"{sig['smart_wallets']} wallets | {sig['conviction']:.0f}% conviction")

                # Stats
                if scan_count % 10 == 0:
                    stats = self.tracker.get_tracker_stats()
                    print(f"\n  Stats: {stats['total_wallets']} wallets | "
                          f"{stats['smart_wallets']} smart | "
                          f"{stats['total_trades_indexed']} trades | "
                          f"Signaux: {self.signals_generated}")

                time.sleep(interval)

            except KeyboardInterrupt:
                print("\n[copy-trader] Arret du monitoring.")
                break
            except Exception as e:
                print(f"[erreur] {e}")
                import traceback
                traceback.print_exc()
                time.sleep(15)

    # =========================================================================
    # NOTIFICATIONS
    # =========================================================================

    def _notify_consensus(self, signal):
        """Notification de consensus smart money."""
        if not self.notifier:
            return

        emoji = "🟢" if signal["direction"] == "YES" else "🔴"
        self.notifier.send(
            f"{emoji} <b>CONSENSUS SMART MONEY</b>\n\n"
            f"📊 <b>{signal['question']}</b>\n\n"
            f"📈 Direction: <b>{signal['direction']}</b>\n"
            f"🧠 Smart wallets: {signal['smart_wallets']}\n"
            f"💪 Conviction: {signal['conviction']:.0f}%\n"
            f"💰 Volume smart: ${signal['volume']:,.0f}\n\n"
            f"💵 YES: {signal['yes_price']:.3f} | NO: {signal['no_price']:.3f}\n\n"
            f"🔗 polymarket.com/event/{signal['slug']}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
        )

    def _notify_wallet_trade(self, trade, wallet_info):
        """Notification de trade d'un wallet suivi."""
        if not self.notifier:
            return

        tier = wallet_info.get("tier", "")
        wr = wallet_info.get("win_rate", 0)

        self.notifier.send(
            f"👁 <b>WALLET TRADE DETECTE</b>\n\n"
            f"🧠 Wallet: <code>{trade['wallet'][:16]}...</code>\n"
            f"🏅 {tier} | WR: {wr:.0%} | Score: {trade['wallet_score']:.0f}\n\n"
            f"📈 {trade['outcome']} @ {trade['price']:.3f}\n"
            f"💰 Taille: {trade['size']:,.0f}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
        )

    # =========================================================================
    # ONE-SHOT REPORTS
    # =========================================================================

    def generate_report(self):
        """
        Genere un rapport complet one-shot.
        Combine tracker stats, smart money flow, et intelligence.
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "tracker_stats": self.tracker.get_tracker_stats(),
            "leaderboard_top10": self.tracker.get_leaderboard(limit=10),
            "smart_wallets": self.tracker.get_smart_wallets(min_score=70),
            "watchlist": self.tracker.get_watchlist(),
            "intelligence": None,
        }

        # Intelligence scan
        try:
            report["intelligence"] = self.detector.full_intelligence_scan()
        except Exception as e:
            report["intelligence"] = {"error": str(e)}

        return report
