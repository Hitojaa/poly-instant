#!/usr/bin/env python3
"""poly-instant - Polymarket advanced analysis, smart money tracking & copy trading engine."""

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


def _load_env():
    """Charge les variables d'environnement."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _load_notifier():
    """Charge le notifier Telegram si configure."""
    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        from polyinstant.notifier import TelegramNotifier
        notifier = TelegramNotifier(token, chat_id)
        print("[telegram] Notifications activees")
        return notifier
    return None


def _get_polygonscan_key():
    """Recupere la cle Polygonscan."""
    _load_env()
    return os.environ.get("POLYGONSCAN_API_KEY", "")


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
            yes_p, no_p, _, _ = PolymarketClient.extract_prices(m)
            if yes_p is None:
                continue
            rows.append([
                m.get("question", "?")[:60],
                f"{yes_p:.2f}",
                f"${float(m.get('volume24hr', 0) or m.get('volume', 0) or 0):,.0f}",
                f"${float(m.get('liquidity', 0) or 0):,.0f}",
            ])
        print(tabulate(rows, headers=["Marche", "YES", "Vol 24h", "Liquidity"]))

    except Exception as e:
        print(f"[erreur] {e}")


# =============================================================================
# COMMANDES ANALYSE AVANCEE
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
    sub_markets = result.get("sub_markets")

    print(f"{'='*70}")
    print(f"  {market['question']}")
    print(f"{'='*70}\n")

    # Si c'est un event multi-marches, afficher le tableau des candidats
    if sub_markets and len(sub_markets) > 1:
        from polyinstant.client import PolymarketClient as _PC
        print("  CANDIDATS / SOUS-MARCHES")
        print(f"  {'—'*40}")
        # Trier par YES price decroissant
        ranked = []
        for sm in sub_markets:
            yp, np_, _, _ = _PC.extract_prices(sm)
            vol = float(sm.get("volume", 0) or sm.get("volume24hr", 0) or 0)
            ranked.append((sm, yp or 0, vol))
        ranked.sort(key=lambda x: x[1], reverse=True)
        for i, (sm, yp, vol) in enumerate(ranked, 1):
            q = sm.get("question", sm.get("slug", "?"))
            # Nettoyer la question pour extraire le nom
            marker = " *" if sm.get("slug") == market.get("slug", "") else ""
            print(f"  {i:>3}. {yp*100:>5.1f}%  ${vol:>12,.0f}  {q}{marker}")
        print(f"\n  ({len(ranked)} marches dans cet event)")
        print(f"  * = marche analyse ci-dessous\n")

    print("  MARCHE ANALYSE")
    print(f"  {'—'*40}")
    print(f"  YES:           {market['yes_price']:.4f} ({market['yes_price']*100:.1f}%)")
    print(f"  NO:            {market['no_price']:.4f} ({market['no_price']*100:.1f}%)")
    print(f"  Total cost:    {market['total_cost']:.4f}")
    print(f"  Spread:        {market['spread']:.4f}")
    print(f"  Volume 24h:    ${market['volume_24h']:,.0f}")
    print(f"  Volume total:  ${market['volume_total']:,.0f}")
    print(f"  Liquidite:     ${market['liquidity']:,.0f}")
    print(f"  Fin:           {market['end_date']}")

    bayesian = result.get("bayesian", {})
    print(f"\n  PROBABILITE BAYESIENNE")
    print(f"  {'—'*40}")
    print(f"  Prior (prix):  {bayesian.get('prior', 0):.4f}")
    print(f"  Posterior:     {bayesian.get('posterior', 0):.4f}")
    print(f"  Ajustement:    {bayesian.get('adjustment', 0):+.4f}")
    print(f"  Confiance:     {bayesian.get('confidence', 0):.0%} ({bayesian.get('confidence_label', '')})")

    kelly = result.get("kelly_adjusted") or result.get("kelly", {}).get("yes", {})
    print(f"\n  KELLY CRITERION")
    print(f"  {'—'*40}")
    print(f"  Full Kelly:    {kelly.get('full_kelly', 0):.1f}% du bankroll")
    print(f"  Half Kelly:    {kelly.get('half_kelly', 0):.1f}% (recommande)")
    print(f"  Quarter Kelly: {kelly.get('quarter_kelly', 0):.1f}% (conservateur)")
    print(f"  Edge:          {kelly.get('edge', 0):+.2f}%")
    print(f"  Verdict:       {kelly.get('recommendation', '')}")

    ev = result.get("ev_adjusted") or result.get("ev", {}).get("yes", {})
    print(f"\n  EXPECTED VALUE")
    print(f"  {'—'*40}")
    print(f"  EV absolue:    {ev.get('ev_absolute', 0):+.4f}")
    print(f"  EV %:          {ev.get('ev_percentage', 0):+.2f}%")
    print(f"  ROI potentiel: {ev.get('potential_roi', 0):+.1f}%")
    print(f"  Breakeven:     {ev.get('breakeven_prob', 0):.2%}")
    print(f"  Edge vs marche:{ev.get('edge_over_market', 0):+.2f}%")
    print(f"  Verdict:       {ev.get('verdict', '')}")

    eff = result.get("efficiency", {})
    print(f"\n  EFFICIENCE DU MARCHE")
    print(f"  {'—'*40}")
    print(f"  Score:         {eff.get('score', 0)}/100")
    print(f"  Label:         {eff.get('label', '')}")
    print(f"  Opportunite:   {'OUI' if eff.get('opportunity') else 'NON'}")

    bs = result.get("book_spreads", {})
    print(f"\n  SPREADS ORDERBOOK")
    print(f"  {'—'*40}")
    print(f"  YES spread:    {bs.get('yes', 0):.4f}")
    print(f"  NO spread:     {bs.get('no', 0):.4f}")

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

    # Smart money flow si disponible
    try:
        from polyinstant.smart_money import SmartMoneyDetector
        detector = SmartMoneyDetector(_get_polygonscan_key())
        flow = detector.smart_money_flow(slug)
        if flow and flow["smart_money"]["trades"] > 0:
            sm = flow["smart_money"]
            print(f"\n  SMART MONEY FLOW")
            print(f"  {'—'*40}")
            print(f"  Smart wallets: {sm['wallets']}")
            print(f"  Trades:        {sm['trades']}")
            print(f"  Biais:         {sm['bias']}")
            print(f"  Conviction:    {sm['conviction']:.0f}%")
            print(f"  YES volume:    ${sm['yes_volume']:,.0f}")
            print(f"  NO volume:     ${sm['no_volume']:,.0f}")

            if flow.get("top_smart_traders"):
                print(f"\n  Top smart traders:")
                for t in flow["top_smart_traders"][:5]:
                    print(f"    {t['address'][:12]}... | {t['outcome']} @ {t['price']:.3f} | "
                          f"${t['cost']:,.0f} | WR: {t['win_rate']:.0%} | {t['tier']}")
    except Exception:
        pass

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
    """Mode monitoring continu - collecte, analyse, notifie."""
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

            print(f"[{now}] {report['markets_scanned']} marches scannes")

            if report["arbitrages"]:
                print(f"\n  ARBITRAGES ({len(report['arbitrages'])})")
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
                print(f"\n  TOP SIGNAUX ({len(report['signals'])})")
                rows = []
                for s in report["signals"][:10]:
                    rows.append([
                        s["market"][:45],
                        f"{s['score']:.0f}",
                        s["direction"],
                        f"{s['confidence']:.0%}",
                        f"{s['yes_price']:.3f}",
                        f"${s['volume_24h']:,.0f}",
                    ])
                print(tabulate(rows, headers=["Marche", "Score", "Direction", "Conf", "YES", "Vol"], tablefmt="simple_outline"))

            if report["whale_alerts"]:
                print(f"\n  BALEINES ({len(report['whale_alerts'])})")
                for w in report["whale_alerts"][:3]:
                    data = w["data"]
                    print(f"    {w['question'][:50]} | {w['side']} | {data['whale_bias']} | {data['whale_pct']:.0f}% volume")

            if report["momentum_alerts"]:
                print(f"\n  MOMENTUM ({len(report['momentum_alerts'])})")
                for m in report["momentum_alerts"][:3]:
                    data = m["data"]
                    print(f"    {m['question'][:50]} | {data['trend']} | RSI: {data['rsi']:.0f}")

            stats = engine.get_stats()
            print(f"\n  Stats: {stats['total_snapshots']} snapshots | {stats['markets_tracked']} marches | Alertes: {stats['alerts_sent']}")

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
        print("Tip: Lance 'monitor' pour collecter de l'historique.")
        return

    print(f"{'='*80}")
    print(f"  {len(report['signals'])} SIGNAUX DE TRADING")
    print(f"{'='*80}\n")

    for i, sig in enumerate(report["signals"][:15], 1):
        marker = "[+]" if sig["direction_score"] > 0 else ("[-]" if sig["direction_score"] < 0 else "[=]")
        print(f"  {i}. {marker} [{sig['score']:.0f}] {sig['market'][:60]}")
        print(f"     Direction: {sig['direction']} ({sig['direction_score']:+.1f}) | Confiance: {sig['confidence']:.0%}")
        print(f"     YES: {sig['yes_price']:.3f} | NO: {sig['no_price']:.3f} | Vol: ${sig['volume_24h']:,.0f}")
        print(f"     Raisons: {' | '.join(sig['reasons'])}")
        print()

    print(f"{'='*80}")
    if report["arbitrages"]:
        print(f"  + {len(report['arbitrages'])} arbitrage(s) detecte(s)")
    if report["whale_alerts"]:
        print(f"  + {len(report['whale_alerts'])} alerte(s) baleine")


def cmd_top(args):
    """Analyse les N meilleurs marches par volume."""
    limit = int(args[0]) if args else 10
    print(f"[poly-instant] Analyse des top {limit} marches par volume...\n")

    client = PolymarketClient()
    markets = client.get_markets(limit=50)

    valid = []
    for m in markets:
        yp, np, _, _ = PolymarketClient.extract_prices(m)
        if yp is not None:
            vol = float(m.get("volume24hr", 0) or m.get("volume", 0) or 0)
            valid.append((vol, m))
    valid.sort(key=lambda x: x[0], reverse=True)

    rows = []
    for vol, market in valid[:limit]:
        yes_price, no_price, _, _ = PolymarketClient.extract_prices(market)
        if yes_price is None:
            continue
        total = yes_price + no_price
        liquidity = float(market.get("liquidity", 0) or 0)

        spread = abs(1.0 - total)
        eff = 100
        if spread > 0.05:
            eff -= 30
        elif spread > 0.02:
            eff -= 15
        if vol < 10000:
            eff -= 20
        if liquidity < 10000:
            eff -= 15

        arb = f"{(1-total)*100:.1f}%" if total < 0.99 else "-"

        rows.append([
            market.get("question", "?")[:50],
            f"{yes_price:.3f}",
            f"{no_price:.3f}",
            f"${vol:,.0f}",
            f"${liquidity:,.0f}",
            arb,
            f"{eff}/100",
        ])

    print(tabulate(rows, headers=["Marche", "YES", "NO", "Vol 24h", "Liquidite", "Arb", "Eff."], tablefmt="simple_outline"))


def cmd_stats(args):
    """Affiche les stats de la base de donnees."""
    collector = DataCollector()
    stats = collector.get_stats()

    print("[poly-instant] Statistiques de la base de donnees\n")
    print(f"  === DONNEES MARCHE ===")
    print(f"  Snapshots prix:    {stats.get('price_snapshots', 0):,}")
    print(f"  Snapshots book:    {stats.get('orderbook_snapshots', 0):,}")
    print(f"  Evenements:        {stats.get('events', 0):,}")
    print(f"  Marches suivis:    {stats.get('market_meta', 0):,}")

    # Wallet tracker stats
    try:
        from polyinstant.wallet_tracker import WalletTracker
        tracker = WalletTracker(_get_polygonscan_key())
        wstats = tracker.get_tracker_stats()
        print(f"\n  === WALLET TRACKER ===")
        print(f"  Wallets indexes:   {wstats.get('total_wallets', 0):,}")
        print(f"  Smart wallets:     {wstats.get('smart_wallets', 0):,}")
        print(f"  Trades indexes:    {wstats.get('total_trades_indexed', 0):,}")
        print(f"  Marches indexes:   {wstats.get('markets_indexed', 0):,}")
        print(f"  Watchlist:         {wstats.get('watchlist_size', 0):,}")
        print(f"  Volume total:      ${wstats.get('total_volume_tracked', 0):,.0f}")
    except Exception:
        pass

    # Marches recents
    tracked = collector.get_tracked_markets()
    if tracked:
        print(f"\n  Derniers marches suivis:")
        for m in tracked[:10]:
            print(f"    {m['slug'][:40]:40s} | {m['last_updated'][:19]}")

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

    display = rows[-30:] if len(rows) > 30 else rows
    print(tabulate(display, headers=["Timestamp", "YES", "NO", "Total", "Vol 24h"]))

    if len(history) > 1:
        yes_prices = [h["yes_price"] for h in history]
        print(f"\n  Min YES: {min(yes_prices):.4f} | Max YES: {max(yes_prices):.4f} | Delta: {yes_prices[-1] - yes_prices[0]:+.4f}")


# =============================================================================
# COMMANDES WALLET TRACKER & SMART MONEY
# =============================================================================

def cmd_index(args):
    """Indexe les trades des marches resolus pour construire la DB de wallets."""
    limit = int(args[0]) if args else 50
    print(f"[poly-instant] Indexation de {limit} marches resolus...\n")

    from polyinstant.wallet_tracker import WalletTracker
    tracker = WalletTracker(_get_polygonscan_key())

    result = tracker.index_resolved_markets(limit=limit)

    print(f"  Marches indexes:  {result['markets_indexed']}")
    print(f"  Trades indexes:   {result['trades_indexed']}")
    print(f"  Wallets mis a jour: {result['wallets_updated']}")

    if result["trades_indexed"] > 0:
        print(f"\n  Scoring des wallets...")
        scored = tracker.score_all_wallets(min_trades=10)
        print(f"  {scored} wallets scores")

    # Stats globales
    stats = tracker.get_tracker_stats()
    print(f"\n  === TOTAL EN DB ===")
    print(f"  Wallets:  {stats['total_wallets']:,}")
    print(f"  Smart:    {stats['smart_wallets']:,}")
    print(f"  Trades:   {stats['total_trades_indexed']:,}")


def cmd_leaderboard(args):
    """Affiche le leaderboard des meilleurs wallets."""
    limit = int(args[0]) if args else 30
    sort_by = args[1] if len(args) > 1 else "composite_score"

    from polyinstant.wallet_tracker import WalletTracker
    tracker = WalletTracker(_get_polygonscan_key())

    print(f"[poly-instant] Leaderboard (top {limit}, tri: {sort_by})\n")

    lb = tracker.get_leaderboard(limit=limit, sort_by=sort_by)
    if not lb:
        print("  Aucun wallet score. Lance 'index' d'abord pour construire la DB.")
        return

    rows = []
    for w in lb:
        rows.append([
            f"#{w['rank']}" if w.get('rank') else "",
            f"{w['address'][:12]}...",
            w.get("tier", "")[:15],
            f"{w['composite_score']:.0f}",
            f"{w['insider_score']:.0f}",
            f"{w['win_rate']:.0%}",
            f"{w['total_trades']}",
            f"{w.get('markets_traded', 0)}",
            f"${w['estimated_pnl']:+,.0f}",
            f"${w['total_volume']:,.0f}",
            f"{w['sharpe']:.2f}",
        ])

    print(tabulate(rows, headers=[
        "Rank", "Wallet", "Tier", "Score", "Insider", "WR", "Trades", "Mkts", "PnL", "Volume", "Sharpe"
    ], tablefmt="simple_outline"))


def cmd_wallet(args):
    """Profil detaille d'un wallet."""
    if not args:
        print("Usage: python main.py wallet <address>")
        return

    address = args[0]
    from polyinstant.wallet_tracker import WalletTracker
    tracker = WalletTracker(_get_polygonscan_key())

    print(f"[poly-instant] Profil de {address[:16]}...\n")

    profile = tracker.get_wallet_profile(address)
    if not profile:
        print(f"  Wallet non trouve en DB. Lance 'index' pour indexer les trades.")
        return

    w = profile["wallet"]
    s = profile.get("scores", {})

    print(f"{'='*70}")
    print(f"  WALLET: {w['address']}")
    print(f"{'='*70}\n")

    print(f"  STATS GENERALES")
    print(f"  {'—'*40}")
    print(f"  Total trades:     {w.get('total_trades', 0)}")
    print(f"  Wins / Losses:    {w.get('total_wins', 0)} / {w.get('total_losses', 0)}")
    print(f"  Win rate:         {w.get('win_rate', 0):.1%}")
    print(f"  Volume total:     ${w.get('total_volume', 0):,.0f}")
    print(f"  PnL estime:       ${w.get('estimated_pnl', 0):+,.0f}")
    print(f"  Smart wallet:     {'OUI' if w.get('is_smart') else 'NON'}")
    print(f"  Label:            {w.get('label', 'Aucun')}")
    print(f"  Tags:             {w.get('tags', '[]')}")

    if s:
        print(f"\n  SCORES")
        print(f"  {'—'*40}")
        print(f"  Composite:        {s.get('composite_score', 0):.0f}/100")
        print(f"  Tier:             {s.get('tier', 'UNKNOWN')}")
        print(f"  Rank:             #{s.get('rank', '?')}")
        print(f"  Insider score:    {s.get('insider_score', 0):.0f}/100")
        print(f"  Sharpe ratio:     {s.get('sharpe_ratio', 0):.3f}")
        print(f"  Timing score:     {s.get('avg_timing_score', 0):.0f}/100")
        print(f"  Consistency:      {s.get('consistency_score', 0):.0f}/100")
        print(f"  Volume score:     {s.get('volume_score', 0):.0f}/100")

    pnl_dist = profile.get("pnl_distribution", {})
    if pnl_dist:
        print(f"\n  DISTRIBUTION PnL")
        print(f"  {'—'*40}")
        print(f"  Moyenne:          ${pnl_dist.get('mean', 0):+.4f}")
        print(f"  Mediane:          ${pnl_dist.get('median', 0):+.4f}")
        print(f"  Ecart-type:       ${pnl_dist.get('stdev', 0):.4f}")
        print(f"  Max gain:         ${pnl_dist.get('max_win', 0):+.4f}")
        print(f"  Max perte:        ${pnl_dist.get('max_loss', 0):+.4f}")
        print(f"  % positifs:       {pnl_dist.get('positive_pct', 0):.0f}%")

    if profile.get("favorite_markets"):
        print(f"\n  MARCHES PREFERES")
        print(f"  {'—'*40}")
        for fm in profile["favorite_markets"][:5]:
            wr = fm['wins'] / fm['trades'] if fm['trades'] > 0 else 0
            print(f"    {fm['question'][:45]} | {fm['trades']} trades | WR: {wr:.0%} | PnL: ${fm['pnl']:+,.0f}")

    if profile.get("recent_trades"):
        print(f"\n  TRADES RECENTS")
        print(f"  {'—'*40}")
        rows = []
        for t in profile["recent_trades"][:15]:
            status = "WIN" if t["won"] else "LOSS"
            rows.append([
                t["timestamp"][:16] if t["timestamp"] else "",
                t["question"][:35] if t.get("question") else "",
                t.get("outcome_bought", ""),
                f"{t['price']:.3f}",
                f"{t['size']:.0f}",
                status,
                f"${t['pnl']:+.2f}",
                f"{t['timing_min']:.0f}m" if t.get("timing_min") else "",
            ])
        print(tabulate(rows, headers=["Date", "Marche", "Side", "Prix", "Size", "W/L", "PnL", "Timing"]))

    print(f"\n{'='*70}")


def cmd_smartmoney(args):
    """Analyse smart money - wallets suspects, clusters, pre-resolution."""
    mode = args[0] if args else "full"

    from polyinstant.smart_money import SmartMoneyDetector
    detector = SmartMoneyDetector(_get_polygonscan_key())

    if mode == "pre-resolution" or mode == "pre":
        print("[poly-instant] Analyse pre-resolution...\n")
        window = int(args[1]) if len(args) > 1 else 120
        result = detector.analyze_pre_resolution(window_min=window)

        summary = result.get("summary", {})
        print(f"  Fenetre: {summary.get('window_minutes', 0)} minutes")
        print(f"  Trades analyses: {summary.get('total_trades', 0)}")
        print(f"  Wallets uniques: {summary.get('unique_wallets', 0)}")
        print(f"  Wallets suspects: {summary.get('suspicious_wallets', 0)}\n")

        if result.get("wallets"):
            rows = []
            for w in result["wallets"][:20]:
                rows.append([
                    f"{w['address'][:12]}...",
                    f"{w['suspicion_score']}/100",
                    f"{w['total_pre_res_trades']}",
                    f"{w['avg_timing_min']:.0f}m",
                    f"${w['total_volume']:,.0f}",
                    f"${w['total_pnl']:+,.0f}",
                    f"{w['avg_entry_price']:.3f}",
                    w.get("label", "")[:25],
                ])
            print(tabulate(rows, headers=[
                "Wallet", "Suspicion", "Trades", "Avg Timing", "Volume", "PnL", "Avg Entry", "Label"
            ], tablefmt="simple_outline"))

        if result.get("patterns"):
            print(f"\n  PATTERNS DETECTES ({len(result['patterns'])})")
            for p in result["patterns"][:10]:
                print(f"    [{p['type']}] {p['description']}")

    elif mode == "sybil" or mode == "clusters":
        print("[poly-instant] Detection de clusters sybil...\n")
        result = detector.detect_sybil_clusters()

        summary = result.get("summary", {})
        print(f"  Clusters trouves: {summary.get('total_clusters', 0)}")
        print(f"  Wallets impliques: {summary.get('unique_wallets_in_clusters', 0)}")
        print(f"  Recidivistes: {summary.get('repeat_offenders', 0)}\n")

        if result.get("clusters"):
            for i, cl in enumerate(result["clusters"][:10], 1):
                print(f"  Cluster #{i}: {cl['description']}")
                print(f"    Marche: {cl['question'][:50]}")
                print(f"    Volume: ${cl['total_volume']:,.0f} | Prix moyen: {cl['avg_price']:.3f}")
                print(f"    Wallets: {', '.join(w[:10]+'...' for w in cl['wallets'][:5])}")
                print()

        if result.get("repeat_offenders"):
            print(f"  RECIDIVISTES (wallets dans plusieurs clusters):")
            for ro in result["repeat_offenders"][:10]:
                print(f"    {ro['address'][:16]}... | {ro['cluster_appearances']} clusters")

    elif mode == "abnormal":
        print("[poly-instant] Detection d'activite anormale...\n")
        hours = int(args[1]) if len(args) > 1 else 24
        result = detector.detect_abnormal_activity(hours=hours)

        summary = result.get("summary", {})
        print(f"  Trades analyses: {summary.get('total_trades', 0)}")
        print(f"  Gros trades: {summary.get('large_trades', 0)}")
        print(f"  Nouvelles baleines: {summary.get('new_whales', 0)}")
        print(f"  Seuil gros trade: ${summary.get('large_threshold', 0):,.0f}\n")

        if result.get("alerts"):
            rows = []
            for a in result["alerts"][:20]:
                rows.append([
                    a.get("severity", ""),
                    a.get("type", ""),
                    f"{a['wallet'][:10]}...",
                    a.get("question", "")[:35],
                    a.get("outcome", ""),
                    f"${a.get('cost', 0):,.0f}",
                    "NEW" if a.get("is_new_wallet") else f"WR:{a.get('wallet_win_rate', 0):.0%}",
                ])
            print(tabulate(rows, headers=[
                "Severity", "Type", "Wallet", "Marche", "Side", "Cost", "Info"
            ], tablefmt="simple_outline"))

    elif mode == "flow":
        if len(args) < 2:
            print("Usage: python main.py smartmoney flow <market_slug>")
            return
        slug = args[1]
        print(f"[poly-instant] Smart money flow pour '{slug}'...\n")
        result = detector.smart_money_flow(slug)

        if not result:
            print("  Pas de donnees smart money pour ce marche.")
            return

        sm = result["smart_money"]
        nm = result["normal_money"]
        sig = result.get("signal", {})

        print(f"  SMART MONEY")
        print(f"    Wallets: {sm['wallets']} | Trades: {sm['trades']}")
        print(f"    YES: ${sm['yes_volume']:,.0f} | NO: ${sm['no_volume']:,.0f}")
        print(f"    Biais: {sm['bias']} | Conviction: {sm['conviction']:.0f}%")

        print(f"\n  NORMAL MONEY")
        print(f"    Wallets: {nm['wallets']} | Trades: {nm['trades']}")
        print(f"    YES: ${nm['yes_volume']:,.0f} | NO: ${nm['no_volume']:,.0f}")

        print(f"\n  SIGNAL")
        print(f"    Direction: {sig.get('direction', 'N/A')}")
        print(f"    Confiance: {sig.get('confidence', 0):.0f}%")
        print(f"    Smart money %: {sig.get('smart_volume_pct', 0):.0f}%")

        if result.get("top_smart_traders"):
            print(f"\n  TOP SMART TRADERS")
            for t in result["top_smart_traders"][:5]:
                print(f"    {t['address'][:12]}... | {t['outcome']} @ {t['price']:.3f} | "
                      f"${t['cost']:,.0f} | WR: {t['win_rate']:.0%} | {t['tier']}")

    else:
        # Full intelligence scan
        print("[poly-instant] Scan d'intelligence complet...\n")
        result = detector.full_intelligence_scan()

        summary = result.get("summary", {})
        print(f"  Wallets suspects:   {summary.get('suspicious_wallets', 0)}")
        print(f"  Clusters sybil:     {summary.get('sybil_clusters', 0)}")
        print(f"  Alertes anormales:  {summary.get('abnormal_alerts', 0)}")
        print(f"  Patterns detectes:  {summary.get('patterns_detected', 0)}")

        pre = result.get("pre_resolution")
        if pre and pre.get("wallets"):
            print(f"\n  TOP WALLETS SUSPECTS:")
            for w in pre["wallets"][:5]:
                print(f"    {w['address'][:12]}... | Suspicion: {w['suspicion_score']}/100 | "
                      f"{w['total_pre_res_trades']} trades | PnL: ${w['total_pnl']:+,.0f}")


def cmd_network(args):
    """Analyse le reseau d'un wallet (wallets lies, co-traders)."""
    if not args:
        print("Usage: python main.py network <wallet_address>")
        return

    address = args[0]
    from polyinstant.smart_money import SmartMoneyDetector
    detector = SmartMoneyDetector(_get_polygonscan_key())

    print(f"[poly-instant] Analyse reseau de {address[:16]}...\n")

    result = detector.analyze_wallet_network(address)

    print(f"  RESEAU DU WALLET")
    print(f"  {'—'*40}")
    print(f"  Wallets lies:       {result['network_size']}")
    print(f"  Connexions smart:   {result['smart_connections']}")

    if result.get("related_wallets"):
        print(f"\n  WALLETS LIES (par co-trading):")
        rows = []
        for rw in result["related_wallets"][:15]:
            rows.append([
                f"{rw['address'][:12]}...",
                f"{rw['co_trades']}",
                f"{rw['win_rate']:.0%}" if rw.get('win_rate') else "?",
                f"${rw['pnl']:+,.0f}" if rw.get('pnl') else "?",
                f"{rw['score']:.0f}" if rw.get('score') else "?",
                rw.get('tier', 'UNKNOWN')[:12],
                "SMART" if rw.get("is_smart") else "",
            ])
        print(tabulate(rows, headers=[
            "Wallet", "Co-trades", "WR", "PnL", "Score", "Tier", "Smart"
        ], tablefmt="simple_outline"))

    if result.get("timing_correlations"):
        print(f"\n  CORRELATIONS SUSPECTES:")
        for tc in result["timing_correlations"][:5]:
            sybil_flag = " *** PROBABLE SYBIL ***" if tc.get("likely_sybil") else ""
            print(f"    {tc['address'][:12]}... | {tc['common_markets']} marches communs | "
                  f"Same outcome: {tc['same_outcome_rate']:.0%}{sybil_flag}")


def cmd_watchlist(args):
    """Gere la watchlist de wallets a suivre."""
    action = args[0] if args else "show"

    from polyinstant.wallet_tracker import WalletTracker
    tracker = WalletTracker(_get_polygonscan_key())

    if action == "add":
        if len(args) < 2:
            print("Usage: python main.py watchlist add <address> [reason]")
            return
        address = args[1]
        reason = " ".join(args[2:]) if len(args) > 2 else ""
        tracker.add_to_watchlist(address, reason=reason, priority=2)
        print(f"  Wallet {address[:16]}... ajoute a la watchlist.")

    elif action == "remove":
        if len(args) < 2:
            print("Usage: python main.py watchlist remove <address>")
            return
        tracker.remove_from_watchlist(args[1])
        print(f"  Wallet retire de la watchlist.")

    elif action == "auto":
        # Ajouter automatiquement les meilleurs wallets
        smart = tracker.get_smart_wallets(min_score=75)
        added = 0
        for w in smart[:20]:
            tracker.add_to_watchlist(
                w["address"],
                reason=f"Auto: score={w['score']:.0f} wr={w['win_rate']:.0%}",
                priority=2,
                auto_copy=False,
            )
            added += 1
        print(f"  {added} smart wallets ajoutes a la watchlist.")

    else:
        # Afficher la watchlist
        print("[poly-instant] Watchlist\n")
        watchlist = tracker.get_watchlist()
        if not watchlist:
            print("  Watchlist vide. Utilise 'watchlist add <address>' ou 'watchlist auto'.")
            return

        rows = []
        for w in watchlist:
            rows.append([
                f"{w['address'][:14]}...",
                f"P{w['priority']}",
                f"{w['score']:.0f}" if w.get('score') else "?",
                w.get("tier", "?")[:12],
                f"{w['win_rate']:.0%}" if w.get('win_rate') else "?",
                f"${w['pnl']:+,.0f}" if w.get('pnl') else "?",
                f"{w['trades']}" if w.get('trades') else "?",
                w.get("reason", "")[:25],
            ])
        print(tabulate(rows, headers=[
            "Wallet", "Prio", "Score", "Tier", "WR", "PnL", "Trades", "Raison"
        ], tablefmt="simple_outline"))


def cmd_copytrade(args):
    """Lance le copy-trading en temps reel."""
    interval = int(args[0]) if args else 60
    notifier = _load_notifier()

    from polyinstant.copy_trader import CopyTrader
    ct = CopyTrader(_get_polygonscan_key(), notifier=notifier)

    print(f"[poly-instant] Copy Trading Monitor")
    print(f"  Interval: {interval}s")
    print(f"  Ctrl+C pour arreter\n")

    ct.run_monitor(interval=interval)


# =============================================================================
# COMMAND REGISTRY
# =============================================================================

COMMANDS = {
    # Base
    "scan": cmd_scan,
    "mispriced": cmd_mispriced,
    "markets": cmd_markets,
    # Analyse avancee
    "analyze": cmd_analyze,
    "monitor": cmd_monitor,
    "signals": cmd_signals,
    "top": cmd_top,
    "stats": cmd_stats,
    "history": cmd_history,
    # Wallet tracker & smart money
    "index": cmd_index,
    "leaderboard": cmd_leaderboard,
    "wallet": cmd_wallet,
    "smartmoney": cmd_smartmoney,
    "network": cmd_network,
    "watchlist": cmd_watchlist,
    "copytrade": cmd_copytrade,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("""
╔═══════════════════════════════════════════════════════════════════════╗
║              poly-instant - Polymarket Intelligence Engine            ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  SCAN & DECOUVERTE                                                    ║
║  ─────────────────                                                    ║
║  scan [min%]           Scanner d'arbitrage en continu                 ║
║  mispriced             Marches a forte conviction                      ║
║  markets [limit]       Lister les marches actifs                       ║
║  top [limit]           Top marches par volume + efficience             ║
║                                                                       ║
║  ANALYSE APPROFONDIE                                                  ║
║  ──────────────────                                                   ║
║  analyze <slug>        Analyse complete (Kelly, EV, Bayesian,         ║
║                        whales, volume, momentum, smart money)          ║
║  signals               Generer les signaux de trading                  ║
║                                                                       ║
║  MONITORING                                                           ║
║  ──────────                                                           ║
║  monitor [sec]         Collecte + analyse + alertes (defaut: 60s)     ║
║  history <slug> [h]    Historique des prix (defaut: 24h)               ║
║  stats                 Stats completes de la DB                        ║
║                                                                       ║
║  WALLET TRACKER & SMART MONEY                                         ║
║  ────────────────────────────                                         ║
║  index [limit]         Indexer les wallets (marches resolus)           ║
║  leaderboard [n] [tri] Classement des meilleurs wallets               ║
║  wallet <address>      Profil complet d'un wallet                     ║
║  network <address>     Analyse reseau (co-traders, sybil)             ║
║  watchlist [add|remove|auto] Gerer la watchlist                       ║
║                                                                       ║
║  INTELLIGENCE                                                         ║
║  ────────────                                                         ║
║  smartmoney [mode]     Modes: full, pre, sybil, abnormal,            ║
║                        flow <slug>                                     ║
║  copytrade [sec]       Copy-trading en temps reel                      ║
║                                                                       ║
║  CONFIG (.env) :                                                      ║
║  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (notifications)               ║
║  POLYGONSCAN_API_KEY  (analyse on-chain, gratuit sur etherscan.io)     ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
""")
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](args)


if __name__ == "__main__":
    main()
