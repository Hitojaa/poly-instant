"""Systeme de notifications Telegram pour les alertes de marche."""

import requests
import time
from datetime import datetime


class TelegramNotifier:
    """
    Envoi d'alertes Telegram formatees pour les opportunites detectees.

    Setup :
    1. Parler a @BotFather sur Telegram -> /newbot -> recuperer le token
    2. Envoyer un message a ton bot
    3. Aller sur https://api.telegram.org/bot<TOKEN>/getUpdates pour trouver ton chat_id
    4. Mettre TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans .env
    """

    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.enabled = bool(bot_token and chat_id)
        self._last_sent = {}  # slug -> timestamp (anti-spam)
        self.cooldown = 300  # 5 minutes entre 2 alertes pour le meme marche

    def send(self, text, parse_mode="HTML"):
        """Envoie un message Telegram."""
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    def _can_send(self, slug):
        """Verifie le cooldown anti-spam."""
        now = time.time()
        last = self._last_sent.get(slug, 0)
        if now - last < self.cooldown:
            return False
        self._last_sent[slug] = now
        return True

    # -------------------------------------------------------------------------
    # ALERTES FORMATEES
    # -------------------------------------------------------------------------

    def alert_arbitrage(self, opportunity):
        """Alerte d'opportunite d'arbitrage."""
        slug = opportunity.get("slug", "")
        if not self._can_send(f"arb_{slug}"):
            return False

        text = (
            f"🔥 <b>ARBITRAGE DETECTE</b>\n\n"
            f"📊 <b>{opportunity.get('question', '?')}</b>\n\n"
            f"💰 YES: {opportunity.get('yes_price', 0):.3f} | NO: {opportunity.get('no_price', 0):.3f}\n"
            f"💵 Cout total: {opportunity.get('total_cost', 0):.3f}\n"
            f"📈 <b>Profit: {opportunity.get('profit_pct', 0):.1f}%</b>\n"
            f"📊 Volume 24h: ${opportunity.get('volume_24h', 0):,.0f}\n"
            f"💧 Liquidite: ${opportunity.get('liquidity', 0):,.0f}\n\n"
            f"🔗 polymarket.com/event/{slug}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        return self.send(text)

    def alert_signal(self, signal):
        """Alerte de signal de trading."""
        slug = signal.get("slug", "")
        if not self._can_send(f"sig_{slug}"):
            return False

        direction_emoji = "🟢" if signal.get("direction_score", 0) > 0 else "🔴"
        reasons = "\n".join(f"  • {r}" for r in signal.get("reasons", []))

        text = (
            f"{direction_emoji} <b>SIGNAL {signal.get('direction', 'NEUTRE')}</b>\n\n"
            f"📊 <b>{signal.get('market', '?')}</b>\n\n"
            f"⭐ Score: {signal.get('score', 0):.0f}/100\n"
            f"🎯 Direction: {signal.get('direction_score', 0):+.1f}\n"
            f"📊 Confiance: {signal.get('confidence', 0):.0%}\n"
            f"💰 YES: {signal.get('yes_price', 0):.3f} | NO: {signal.get('no_price', 0):.3f}\n"
            f"📊 Volume 24h: ${signal.get('volume_24h', 0):,.0f}\n\n"
            f"<b>Raisons:</b>\n{reasons}\n\n"
            f"🔗 polymarket.com/event/{slug}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        return self.send(text)

    def alert_whale(self, slug, question, whale_data):
        """Alerte de detection de baleine."""
        if not self._can_send(f"whale_{slug}"):
            return False

        text = (
            f"🐋 <b>BALEINE DETECTEE</b>\n\n"
            f"📊 <b>{question}</b>\n\n"
            f"🐋 {whale_data.get('whale_count', 0)} ordres de baleines\n"
            f"💰 Volume baleines: ${whale_data.get('whale_volume', 0):,.0f}\n"
            f"📊 {whale_data.get('whale_pct', 0):.0f}% du volume total\n"
            f"📈 Biais: <b>{whale_data.get('whale_bias', 'NEUTRAL')}</b>\n\n"
            f"🔗 polymarket.com/event/{slug}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        return self.send(text)

    def alert_momentum(self, slug, question, momentum_data):
        """Alerte de changement de momentum."""
        if not self._can_send(f"mom_{slug}"):
            return False

        signal = momentum_data.get("signal", "NEUTRAL")
        if signal in ("NEUTRAL",):
            return False

        emoji = "🚀" if "UP" in signal else "📉"
        text = (
            f"{emoji} <b>MOMENTUM {momentum_data.get('trend', 'NEUTRE')}</b>\n\n"
            f"📊 <b>{question}</b>\n\n"
            f"📈 RSI: {momentum_data.get('rsi', 50):.0f}\n"
            f"💨 Momentum: {momentum_data.get('momentum', 0):+.2f}%\n"
            f"🌊 Volatilite: {momentum_data.get('volatility', 0):.2f}%\n"
            f"📊 Changement: {momentum_data.get('price_change_pct', 0):+.1f}%\n"
            f"💰 Prix actuel: {momentum_data.get('last_price', 0):.3f}\n\n"
            f"🔗 polymarket.com/event/{slug}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        return self.send(text)

    def alert_efficiency(self, slug, question, efficiency_data):
        """Alerte de marche inefficient."""
        if not self._can_send(f"eff_{slug}"):
            return False

        score = efficiency_data.get("score", 100)
        if score >= 50:
            return False  # Pas d'alerte si marche efficient

        text = (
            f"⚡ <b>MARCHE INEFFICIENT</b>\n\n"
            f"📊 <b>{question}</b>\n\n"
            f"📉 Score d'efficience: {score}/100\n"
            f"🏷️ {efficiency_data.get('label', '')}\n\n"
            f"Detail:\n"
            f"  • Deviation prix: {efficiency_data.get('detail', {}).get('price_deviation', 0):.4f}\n"
            f"  • Spread book: {efficiency_data.get('detail', {}).get('avg_book_spread', 0):.4f}\n"
            f"  • Volume 24h: ${efficiency_data.get('detail', {}).get('volume_24h', 0):,.0f}\n\n"
            f"🔗 polymarket.com/event/{slug}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        return self.send(text)

    def send_daily_report(self, stats):
        """Rapport quotidien."""
        text = (
            f"📋 <b>RAPPORT QUOTIDIEN</b>\n"
            f"{'='*30}\n\n"
            f"📊 Marches suivis: {stats.get('markets_tracked', 0)}\n"
            f"🔍 Scans effectues: {stats.get('scans_count', 0)}\n"
            f"🔥 Arbitrages detectes: {stats.get('arb_count', 0)}\n"
            f"📡 Signaux generes: {stats.get('signals_count', 0)}\n"
            f"🐋 Baleines detectees: {stats.get('whale_count', 0)}\n\n"
            f"📊 Meilleur arbitrage: {stats.get('best_arb', 'Aucun')}\n"
            f"⭐ Meilleur signal: {stats.get('best_signal', 'Aucun')}\n\n"
            f"💾 Snapshots en DB: {stats.get('total_snapshots', 0)}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        return self.send(text)

    def send_startup(self):
        """Message de demarrage du bot."""
        text = (
            f"🤖 <b>poly-instant demarre</b>\n\n"
            f"📡 Scanner actif\n"
            f"🔔 Notifications activees\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send(text)
