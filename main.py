#!/usr/bin/env python3
"""poly-instant - Polymarket advanced analysis & signal engine."""

import sys
import os
import time
from datetime import datetime
from tabulate import tabulate

from polyinstant.scanner import OpportunityScanner
from polyinstant.analyzer import MarketAnalyzer
from polyinstant.client import PolymarketClient
from polyinstant.analytics import AdvancedAnalytics
from polyinstant.collector import DataCollector
from polyinstant.signals import SignalEngine


def _load_notifier():
    """Charge le notifier Telegram si configure."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        from polyinstant.notifier import TelegramNotifier
        notifier = TelegramNotifier(token, chat_id)
        print("[telegram] Notifications activees")
        return notifier
    return None


# =============================================================================
# COMMANDES DE BASE
# =============================================================================

def cmd_scan(args):
    """Scan pour des opportunites d'arbitrage en continu."""
    min_profit = float(args[0]) if args else 2.0
    scanner = OpportunityScanner(min_profit_pct=min_profit)

    print(f"[poly-instant] Scan arbitrage (min profit: {min_profit}%)")
    print(f"[poly-instant] Ctrl+C pour arreter\n")

    while True:
        try:
            opps = scanner.scan_arbitrage(limit=100)
            now = datetime.now().strftime("%H:%M:%S")

            if opps:
                rows = []
                for o in opps[:10]:
                    rows.append([
                        o["question"][:55],
                        f"{o['yes_price']:.3f}",
                        f"{o['no_price']:.3f}",
                        f"{o['total_cost']:.3f}",
                        f"{o['profit_pct']:.1f}%",
                        f"${o['volume_24h']:,.0f}",
                    ])
                print(f"\n[{now}] {len(opps)} opportunite(s) trouvee(s):")
                print(tabulate(rows, headers=["Marche", "YES", "NO", "Total", "Profit", "Vol 24h"]))
            else:
                print(f"[{now}] Aucune opportunite d'arbitrage")

            time.sleep(30)

        except KeyboardInterrupt:
            print("\nArret.")
            break
        except Exception as e:
            print(f"[erreur] {e}")
            time.sleep(10)


def cmd_mispriced(args):
    """Affiche les marches avec forte conviction."""
    scanner = OpportunityScanner()
    print("[poly-instant] Marches a forte conviction\n")

    try:
        results = scanner.scan_mispriced(limit=100)
        if not results:
            print("Rien de notable pour l'instant.")
            return

        rows = []
        for r in results[:15]:
            rows.append([
                r["question"][:55],
                r["conviction"],
                f"{r['conviction_pct']:.0f}%",
                f"${r['volume_24h']:,.0f}",
                f"${r['liquidity']:,.0f}",
            ])
        print(tabulate(rows, headers=["Marche", "Side", "Conviction", "Vol 24h", "Liquidity"]))

    except Exception as e:
        print(f"[erreur] {e}")


def cmd_markets(args):
    """Liste les marches actifs."""
    client = PolymarketClient()
    limit = int(args[0]) if args else 20

    print(f"[poly-instant] Top {limit} marches actifs\n")

    try:
        markets = client.get_markets(limit=limit)
        rows = []
        for m in markets:
            tokens = m.get("tokens", [])
            yes_p = float(tokens[0].get("price", 0)) if tokens else 0
            rows.append([
                m.get("question", "?")[:60],
                f"{yes_p:.2f}",
                f"${float(m.get('volume24hr', 0) or 0):,.0f}",
                f"${float(m.get('liquidity', 0) or 0):,.0f}",
            ])
        print(tabulate(rows, headers=["Marche", "YES", "Vol 24h", "Liquidity"]))

    except Exception as e:
        print(f"[erreur] {e}")


# =============================================================================
# COMMANDES AVANCEES
# =============================================================================

def cmd_analyze(args):
    """Analyse detaillee d'un marche avec tous les indicateurs."""
    if not args:
        print("Usage: python main.py analyze <market_slug>")
        return

    slug = args[0]
    print(f"[poly-instant] Analyse approfondie de '{slug}'...\n")

    engine = SignalEngine()
    result = engine.analyze_single(slug)

    if not result:
        print(f"Marche '{slug}' non trouve.")
        return

    market = result["market"]

    # Header
    print(f"{'='*70}")
    print(f"  {market['question']}")
    print(f"{'='*70}\n")

    # Prix et marche
    print("  MARCHE")
    print(f"  {'—'*40}")
    print(f"  YES:           {market['yes_price']:.4f} ({market['yes_price']*100:.1f}%)")
    print(f"  NO:            {market['no_price']:.4f} ({market['no_price']*100:.1f}%)")
    print(f"  Total cost:    {market['total_cost']:.4f}")
    print(f"  Spread:        {market['spread']:.4f}")
    print(f"  Volume 24h:    ${market['volume_24h']:,.0f}")
    print(f"  Volume total:  ${market['volume_total']:,.0f}")
    print(f"  Liquidite:     ${market['liquidity']:,.0f}")
    print(f"  Fin:           {market['end_date']}")

    # Bayesian
    bayesian = result.get("bayesian", {})
    print(f"\n  PROBABILITE BAYESIENNE")
    print(f"  {'—'*40}")
    print(f"  Prior (prix):  {bayesian.get('prior', 0):.4f}")
    print(f"  Posterior:     {bayesian.get('posterior', 0):.4f}")
    print(f"  Ajustement:    {bayesian.get('adjustment', 0):+.4f}")
    print(f"  Confiance:     {bayesian.get('confidence', 0):.0%} ({bayesian.get('confidence_label', '')})")

    # Kelly
    kelly = result.get("kelly_adjusted") or result.get("kelly", {}).get("yes", {})
    print(f"\n  KELLY CRITERION")
    print(f"  {'—'*40}")
    print(f"  Full Kelly:    {kelly.get('full_kelly', 0):.1f}% du bankroll")
    print(f"  Half Kelly:    {kelly.get('half_kelly', 0):.1f}% (recommande)")
    print(f"  Quarter Kelly: {kelly.get('quarter_kelly', 0):.1f}% (conservateur)")
    print(f"  Edge:          {kelly.get('edge', 0):+.2f}%")
    print(f"  Verdict:       {kelly.get('recommendation', '')}")

    # EV
    ev = result.get("ev_adjusted") or result.get("ev", {}).get("yes", {})
    print(f"\n  EXPECTED VALUE")
    print(f"  {'—'*40}")
    print(f"  EV absolue:    {ev.get('ev_absolute', 0):+.4f}")
    print(f"  EV %:          {ev.get('ev_percentage', 0):+.2f}%")
    print(f"  ROI potentiel: {ev.get('potential_roi', 0):+.1f}%")
    print(f"  Breakeven:     {ev.get('breakeven_prob', 0):.2%}")
    print(f"  Edge vs marche:{ev.get('edge_over_market', 0):+.2f}%")
    print(f"  Verdict:       {ev.get('verdict', '')}")

    # Efficiency
    eff = result.get("efficiency", {})
    print(f"\n  EFFICIENCE DU MARCHE")
    print(f"  {'—'*40}")
    print(f"  Score:         {eff.get('score', 0)}/100")
    print(f"  Label:         {eff.get('label', '')}")
    print(f"  Opportunite:   {'OUI' if eff.get('opportunity') else 'NON'}")

    # Orderbook spreads
    bs = result.get("book_spreads", {})
    print(f"\n  SPREADS ORDERBOOK")
    print(f"  {'—'*40}")
    print(f"  YES spread:    {bs.get('yes', 0):.4f}")
    print(f"  NO spread:     {bs.get('no', 0):.4f}")

    # Volume profile
    for side in ["yes", "no"]:
        vp = result.get("volume_profile", {}).get(side)
        if vp:
            print(f"\n  VOLUME PROFILE ({side.upper()})")
            print(f"  {'—'*40}")
            print(f"  POC (prix):    {vp.get('poc_price', 0):.4f} (vol: {vp.get('poc_volume', 0):,.0f})")
            print(f"  Value Area:    {vp.get('value_area_low', 0):.4f} - {vp.get('value_area_high', 0):.4f}")
            print(f"  Bid total:     {vp.get('bid_total', 0):,.0f}")
            print(f"  Ask total:     {vp.get('ask_total', 0):,.0f}")
            print(f"  Buy/Sell:      {vp.get('buy_sell_ratio', 0):.3f}")
            print(f"  Pression:      {vp.get('pressure', '')}")

            if vp.get("support_zones"):
                print(f"  Supports:      ", end="")
                print(" | ".join(f"{s['price']:.3f} ({s['volume']:,.0f})" for s in vp["support_zones"][:3]))
            if vp.get("resistance_zones"):
                print(f"  Resistances:   ", end="")
                print(" | ".join(f"{r['price']:.3f} ({r['volume']:,.0f})" for r in vp["resistance_zones"][:3]))

    # Whales
    for side in ["yes", "no"]:
        whales = result.get("whales", {}).get(side)
        if whales and whales.get("whale_count", 0) > 0:
            print(f"\n  BALEINES ({side.upper()})")
            print(f"  {'—'*40}")
            print(f"  Nb ordres:     {whales['whale_count']}")
            print(f"  Volume:        ${whales['whale_volume']:,.0f} ({whales['whale_pct']:.0f}% du total)")
            print(f"  Biais:         {whales['whale_bias']}")
            print(f"  Seuil baleine: {whales['whale_threshold']:,.0f}")
            if whales.get("whale_orders"):
                print(f"  Top ordres:")
                for w in whales["whale_orders"][:5]:
                    print(f"    {w['side']} @ {w['price']:.4f} - taille: {w['size']:,.0f} ({w['ratio_vs_median']:.0f}x median)")

    # Momentum
    momentum = result.get("momentum")
    if momentum:
        print(f"\n  MOMENTUM (sur {result.get('history_points', 0)} points)")
        print(f"  {'—'*40}")
        print(f"  Tendance:      {momentum.get('trend', 'N/A')}")
        print(f"  RSI:           {momentum.get('rsi', 50):.0f}")
        print(f"  Momentum:      {momentum.get('momentum', 0):+.2f}%")
        print(f"  Volatilite:    {momentum.get('volatility', 0):.2f}%")
        print(f"  Acceleration:  {momentum.get('acceleration', 0):+.2f}%")
        print(f"  Chgmt prix:    {momentum.get('price_change_pct', 0):+.1f}%")
        print(f"  Signal:        {momentum.get('signal', 'NEUTRAL')}")
    else:
        print(f"\n  MOMENTUM")
        print(f"  {'—'*40}")
        print(f"  Pas assez d'historique. Lance 'monitor' pour collecter.")

    # Prediction
    pred = result.get("prediction", {})
    print(f"\n  PREDICTION")
    print(f"  {'—'*40}")
    print(f"  Direction:     {pred.get('prediction', 'N/A')} (score: {pred.get('direction_score', 0):+.1f})")
    print(f"  Confiance:     {pred.get('confidence', 0):.0%}")
    if pred.get("signals"):
        for sig in pred["signals"]:
            print(f"    [{sig['name']}] score={sig['score']:+.0f} conf={sig['confidence']:.0%}")

    print(f"\n{'='*70}")


def cmd_monitor(args):
    """
    Mode monitoring continu - collecte, analyse, notifie.
    C'est le mode principal pour tourner en fond.
    """
    interval = int(args[0]) if args else 60
    notifier = _load_notifier()
    engine = SignalEngine(notifier=notifier)

    print(f"[poly-instant] Mode MONITORING (interval: {interval}s)")
    print(f"[poly-instant] Collecte de donnees + analyse + alertes")
    if notifier:
        notifier.send_startup()
    print(f"[poly-instant] Ctrl+C pour arreter\n")

    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{now}] Scan en cours...")

            report = engine.full_scan(limit=100)

            # Resume
            print(f"[{now}] {report['markets_scanned']} marches scannes")

            if report["arbitrages"]:
                print(f"\n  🔥 ARBITRAGES ({len(report['arbitrages'])})")
                rows = []
                for a in report["arbitrages"][:5]:
                    rows.append([
                        a["question"][:50],
                        f"{a['yes_price']:.3f}",
                        f"{a['no_price']:.3f}",
                        f"{a['profit_pct']:.1f}%",
                        f"${a['volume_24h']:,.0f}",
                    ])
                print(tabulate(rows, headers=["Marche", "YES", "NO", "Profit", "Vol 24h"], tablefmt="simple_outline"))

            if report["signals"]:
                print(f"\n  📡 TOP SIGNAUX ({len(report['signals'])})")
                rows = []
                for s in report["signals"][:10]:
                    rows.append([
                        s["market"][:45],
                        f"{s['score']:.0f}",
                        s["direction"],
                        f"{s['confidence']:.0%}",
                        f"{s['yes_price']:.3f}",
                        f"${s['volume_24h']:,.0f}",
                        " | ".join(s["reasons"][:2]),
                    ])
                print(tabulate(rows, headers=["Marche", "Score", "Direction", "Conf", "YES", "Vol", "Raisons"], tablefmt="simple_outline"))

            if report["whale_alerts"]:
                print(f"\n  🐋 BALEINES ({len(report['whale_alerts'])})")
                for w in report["whale_alerts"][:3]:
                    data = w["data"]
                    print(f"    {w['question'][:50]} | {w['side']} | {data['whale_bias']} | {data['whale_pct']:.0f}% volume")

            if report["momentum_alerts"]:
                print(f"\n  🚀 MOMENTUM ({len(report['momentum_alerts'])})")
                for m in report["momentum_alerts"][:3]:
                    data = m["data"]
                    print(f"    {m['question'][:50]} | {data['trend']} | RSI: {data['rsi']:.0f}")

            if report["efficiency_alerts"]:
                print(f"\n  ⚡ INEFFICIENTS ({len(report['efficiency_alerts'])})")
                for e in report["efficiency_alerts"][:3]:
                    data = e["data"]
                    print(f"    {e['question'][:50]} | Score: {data['score']}/100")

            # Stats
            stats = engine.get_stats()
            print(f"\n  📊 Stats: {stats['total_snapshots']} snapshots | {stats['markets_tracked']} marches | Alertes: {stats['alerts_sent']}")

            time.sleep(interval)

        except KeyboardInterrupt:
            print("\n\n[poly-instant] Arret du monitoring.")
            stats = engine.get_stats()
            print(f"  Scans effectues: {stats['scans_count']}")
            print(f"  Alertes envoyees: {stats['alerts_sent']}")
            break
        except Exception as e:
            print(f"[erreur] {e}")
            import traceback
            traceback.print_exc()
            time.sleep(15)


def cmd_signals(args):
    """Genere les signaux de trading actuels (one-shot)."""
    print("[poly-instant] Generation des signaux de trading...\n")

    engine = SignalEngine()
    report = engine.full_scan(limit=100)

    if not report["signals"]:
        print("Aucun signal assez fort pour le moment.")
        print("Tip: Lance 'monitor' pour collecter de l'historique et avoir des signaux de momentum.")
        return

    print(f"{'='*80}")
    print(f"  {len(report['signals'])} SIGNAUX DE TRADING")
    print(f"{'='*80}\n")

    for i, sig in enumerate(report["signals"][:15], 1):
        direction_marker = "🟢" if sig["direction_score"] > 0 else ("🔴" if sig["direction_score"] < 0 else "⚪")
        print(f"  {i}. {direction_marker} [{sig['score']:.0f}] {sig['market'][:60]}")
        print(f"     Direction: {sig['direction']} ({sig['direction_score']:+.1f}) | Confiance: {sig['confidence']:.0%}")
        print(f"     YES: {sig['yes_price']:.3f} | NO: {sig['no_price']:.3f} | Vol: ${sig['volume_24h']:,.0f}")
        print(f"     Raisons: {' | '.join(sig['reasons'])}")
        print()

    # Resume
    print(f"{'='*80}")
    if report["arbitrages"]:
        print(f"  + {len(report['arbitrages'])} arbitrage(s) detecte(s)")
    if report["whale_alerts"]:
        print(f"  + {len(report['whale_alerts'])} alerte(s) baleine")
    if report["efficiency_alerts"]:
        print(f"  + {len(report['efficiency_alerts'])} marche(s) inefficient(s)")


def cmd_top(args):
    """Analyse les N meilleurs marches par volume."""
    limit = int(args[0]) if args else 10
    print(f"[poly-instant] Analyse des top {limit} marches par volume...\n")

    client = PolymarketClient()
    analytics = AdvancedAnalytics(client)
    markets = client.get_markets(limit=50)

    # Trier par volume
    valid = []
    for m in markets:
        tokens = m.get("tokens", [])
        if len(tokens) == 2:
            vol = float(m.get("volume24hr", 0) or 0)
            valid.append((vol, m))
    valid.sort(key=lambda x: x[0], reverse=True)

    rows = []
    for vol, market in valid[:limit]:
        tokens = market.get("tokens", [])
        yes_price = float(tokens[0].get("price", 0))
        no_price = float(tokens[1].get("price", 0))
        total = yes_price + no_price
        liquidity = float(market.get("liquidity", 0) or 0)

        # Quick efficiency check
        spread = abs(1.0 - total)
        eff_score = 100
        if spread > 0.05:
            eff_score -= 30
        elif spread > 0.02:
            eff_score -= 15
        if vol < 10000:
            eff_score -= 20
        if liquidity < 10000:
            eff_score -= 15

        arb = f"{(1-total)*100:.1f}%" if total < 0.99 else "-"

        rows.append([
            market.get("question", "?")[:50],
            f"{yes_price:.3f}",
            f"{no_price:.3f}",
            f"${vol:,.0f}",
            f"${liquidity:,.0f}",
            arb,
            f"{eff_score}/100",
        ])

    print(tabulate(rows, headers=["Marche", "YES", "NO", "Vol 24h", "Liquidite", "Arb", "Eff."], tablefmt="simple_outline"))


def cmd_stats(args):
    """Affiche les stats de la base de donnees."""
    collector = DataCollector()
    stats = collector.get_stats()

    print("[poly-instant] Statistiques de la base de donnees\n")
    print(f"  Snapshots prix:    {stats.get('price_snapshots', 0):,}")
    print(f"  Snapshots book:    {stats.get('orderbook_snapshots', 0):,}")
    print(f"  Evenements:        {stats.get('events', 0):,}")
    print(f"  Marches suivis:    {stats.get('market_meta', 0):,}")

    # Marches recents
    tracked = collector.get_tracked_markets()
    if tracked:
        print(f"\n  Derniers marches suivis:")
        for m in tracked[:10]:
            print(f"    {m['slug'][:40]:40s} | {m['last_updated'][:19]}")

    # Evenements recents
    events = collector.get_recent_events(hours=24)
    if events:
        print(f"\n  Evenements recents ({len(events)}):")
        for e in events[:10]:
            print(f"    [{e['severity']}] {e['event_type']:12s} | {e['slug'][:30]} | {e['message'][:40]}")


def cmd_history(args):
    """Affiche l'historique des prix d'un marche."""
    if not args:
        print("Usage: python main.py history <slug> [hours]")
        return

    slug = args[0]
    hours = int(args[1]) if len(args) > 1 else 24
    collector = DataCollector()

    history = collector.get_price_history(slug, hours)
    if not history:
        print(f"Pas d'historique pour '{slug}'.")
        print("Lance 'monitor' pour commencer a collecter.")
        return

    print(f"[poly-instant] Historique de '{slug}' ({hours}h) - {len(history)} points\n")

    rows = []
    for h in history:
        rows.append([
            h["timestamp"][:19],
            f"{h['yes_price']:.4f}",
            f"{h['no_price']:.4f}",
            f"{h['total_cost']:.4f}",
            f"${h['volume_24h']:,.0f}",
        ])

    # Afficher les 30 derniers points
    display = rows[-30:] if len(rows) > 30 else rows
    print(tabulate(display, headers=["Timestamp", "YES", "NO", "Total", "Vol 24h"]))

    # Stats
    if len(history) > 1:
        yes_prices = [h["yes_price"] for h in history]
        print(f"\n  Min YES: {min(yes_prices):.4f} | Max YES: {max(yes_prices):.4f} | Delta: {yes_prices[-1] - yes_prices[0]:+.4f}")


# =============================================================================
# COMMAND REGISTRY
# =============================================================================

COMMANDS = {
    # Basiques
    "scan": cmd_scan,
    "mispriced": cmd_mispriced,
    "markets": cmd_markets,
    # Avances
    "analyze": cmd_analyze,
    "monitor": cmd_monitor,
    "signals": cmd_signals,
    "top": cmd_top,
    "stats": cmd_stats,
    "history": cmd_history,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("""
╔══════════════════════════════════════════════════════════════╗
║            poly-instant - Polymarket Analysis Engine         ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  SCAN & DECOUVERTE                                           ║
║  ─────────────────                                           ║
║  scan [min%]        Scanner d'arbitrage en continu           ║
║  mispriced          Marches a forte conviction                ║
║  markets [limit]    Lister les marches actifs                 ║
║  top [limit]        Top marches par volume + analyse rapide   ║
║                                                              ║
║  ANALYSE APPROFONDIE                                         ║
║  ──────────────────                                          ║
║  analyze <slug>     Analyse complete (Kelly, EV, Bayesian,   ║
║                     whales, volume profile, momentum...)      ║
║  signals            Generer les signaux de trading            ║
║                                                              ║
║  MONITORING                                                  ║
║  ──────────                                                  ║
║  monitor [sec]      Mode continu : collecte + analyse +      ║
║                     alertes Telegram (defaut: 60s)            ║
║  history <slug> [h] Historique des prix (defaut: 24h)         ║
║  stats              Stats de la base de donnees               ║
║                                                              ║
║  CONFIG TELEGRAM (optionnel, dans .env) :                    ║
║  TELEGRAM_BOT_TOKEN=xxx                                      ║
║  TELEGRAM_CHAT_ID=xxx                                        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](args)


if __name__ == "__main__":
    main()
