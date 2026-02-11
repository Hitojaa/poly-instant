#!/usr/bin/env python3
"""poly-instant - Polymarket scanner & trading toolkit."""

import sys
import time
from datetime import datetime
from tabulate import tabulate

from polyinstant.scanner import OpportunityScanner
from polyinstant.analyzer import MarketAnalyzer


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


def cmd_analyze(args):
    """Analyse detaillee d'un marche."""
    if not args:
        print("Usage: python main.py analyze <market_slug>")
        return

    slug = args[0]
    analyzer = MarketAnalyzer()
    print(f"[poly-instant] Analyse de '{slug}'...\n")

    result = analyzer.analyze(slug)
    if not result:
        print(f"Marche '{slug}' non trouve.")
        return

    if "error" in result:
        print(result["error"])
        return

    print(f"  Question:    {result['question']}")
    print(f"  Fin:         {result['end_date']}")
    print(f"  YES:         {result['yes_price']:.3f}")
    print(f"  NO:          {result['no_price']:.3f}")
    print(f"  Spread:      {result['spread']:.4f}")
    print(f"  Vol 24h:     ${result['volume_24h']:,.0f}")
    print(f"  Vol total:   ${result['volume_total']:,.0f}")
    print(f"  Liquidite:   ${result['liquidity']:,.0f}")
    print(f"  Arb opp:     {'OUI' if result['arb_opportunity'] else 'NON'} ({result['arb_profit_pct']:.2f}%)")

    for side, book in [("YES", result["yes_orderbook"]), ("NO", result["no_orderbook"])]:
        if book:
            print(f"\n  Orderbook {side}:")
            print(f"    Best bid:   {book['best_bid']:.4f}")
            print(f"    Best ask:   {book['best_ask']:.4f}")
            print(f"    Spread:     {book['spread']:.4f}")
            print(f"    Bid depth:  {book['bid_depth_10']:.0f}")
            print(f"    Ask depth:  {book['ask_depth_10']:.0f}")


def cmd_markets(args):
    """Liste les marches actifs."""
    from polyinstant.client import PolymarketClient
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


COMMANDS = {
    "scan": cmd_scan,
    "mispriced": cmd_mispriced,
    "analyze": cmd_analyze,
    "markets": cmd_markets,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("poly-instant - Polymarket scanner")
        print()
        print("Commandes:")
        print("  scan [min_profit%]   Scanner d'arbitrage en continu")
        print("  mispriced            Marches a forte conviction")
        print("  analyze <slug>       Analyse detaillee d'un marche")
        print("  markets [limit]      Lister les marches actifs")
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](args)


if __name__ == "__main__":
    main()
