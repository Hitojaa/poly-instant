"""Systeme de tracking et scoring de wallets Polymarket."""

import sqlite3
import json
import time
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from .blockchain import PolygonClient
from .client import PolymarketClient

DB_PATH = Path(__file__).parent.parent / "data" / "polymarket.db"


class WalletTracker:
    """
    Indexe, track et score les wallets qui tradent sur Polymarket.

    Pipeline :
    1. Collecter les trades de marches resolus
    2. Mapper chaque trade a un wallet
    3. Calculer win/loss pour chaque wallet
    4. Scorer les wallets (win rate, PnL, timing, volume)
    5. Identifier les "smart wallets" (win rate > seuil avec sample suffisant)
    6. Stocker tout en SQLite

    Tables SQLite :
    - wallets : profil de chaque wallet
    - wallet_trades : historique des trades par wallet
    - wallet_scores : scores calcules
    - tracked_wallets : watchlist de wallets a suivre
    """

    def __init__(self, polygonscan_api_key="", db_path=None):
        self.db_path = db_path or DB_PATH
        self.poly_client = PolymarketClient()
        self.chain = PolygonClient(polygonscan_api_key)
        self._ensure_db()

    def _ensure_db(self):
        """Cree les tables wallet si necessaire."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                address TEXT PRIMARY KEY,
                first_seen TEXT,
                last_seen TEXT,
                total_trades INTEGER DEFAULT 0,
                total_wins INTEGER DEFAULT 0,
                total_losses INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                total_volume REAL DEFAULT 0,
                estimated_pnl REAL DEFAULT 0,
                avg_entry_price REAL DEFAULT 0,
                avg_timing_minutes REAL DEFAULT 0,
                markets_traded INTEGER DEFAULT 0,
                label TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                is_smart INTEGER DEFAULT 0,
                smart_score REAL DEFAULT 0
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS wallet_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                market_slug TEXT,
                market_question TEXT,
                condition_id TEXT,
                token_id TEXT,
                side TEXT,
                outcome_bought TEXT,
                price REAL,
                size REAL,
                total_cost REAL,
                market_resolved INTEGER DEFAULT 0,
                resolution TEXT,
                trade_won INTEGER DEFAULT 0,
                pnl REAL DEFAULT 0,
                time_before_resolution_min REAL DEFAULT 0,
                role TEXT DEFAULT 'unknown',
                raw_data TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS wallet_scores (
                address TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                win_rate REAL DEFAULT 0,
                win_rate_weighted REAL DEFAULT 0,
                avg_pnl_per_trade REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                sharpe_ratio REAL DEFAULT 0,
                avg_timing_score REAL DEFAULT 0,
                consistency_score REAL DEFAULT 0,
                volume_score REAL DEFAULT 0,
                insider_score REAL DEFAULT 0,
                composite_score REAL DEFAULT 0,
                rank INTEGER DEFAULT 0,
                tier TEXT DEFAULT 'UNKNOWN'
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS tracked_wallets (
                address TEXT PRIMARY KEY,
                added_at TEXT NOT NULL,
                reason TEXT,
                priority INTEGER DEFAULT 1,
                auto_copy INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            )
        """)

        # Index
        c.execute("CREATE INDEX IF NOT EXISTS idx_wt_address ON wallet_trades(wallet_address)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wt_market ON wallet_trades(market_slug)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wt_ts ON wallet_trades(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wallets_smart ON wallets(is_smart)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ws_score ON wallet_scores(composite_score)")

        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    # =========================================================================
    # INDEXATION - Collecter les trades et les mapper aux wallets
    # =========================================================================

    def index_resolved_markets(self, limit=50):
        """
        Indexe les trades de marches recemment resolus.
        C'est la methode principale pour construire la base de donnees.

        Pour chaque marche resolu :
        1. Recupere tous les trades
        2. Determine le winning outcome
        3. Pour chaque trade, calcule win/loss et PnL
        4. Stocke en DB
        """
        print(f"  Recuperation des marches resolus...", flush=True)
        resolved = self.chain.get_resolved_markets(limit=limit)
        print(f"  {len(resolved)} marches resolus trouves\n", flush=True)
        conn = self._conn()
        c = conn.cursor()
        indexed_count = 0
        markets_skipped_no_res = 0
        markets_skipped_dup = 0
        markets_skipped_no_trades = 0
        markets_with_trades = 0
        wallet_updates = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0, "volume": 0, "trades": 0, "markets": set()})

        for i, market in enumerate(resolved):
            slug = market.get("slug", market.get("market_slug", ""))
            question = market.get("question", market.get("title", "?"))
            condition_id = market.get("conditionId", market.get("condition_id", ""))
            end_date_str = market.get("endDate", market.get("end_date", market.get("endDateIso", "")))

            progress = f"[{i+1}/{len(resolved)}]"

            # Determiner le resultat
            resolution = self.chain.get_market_resolution(market)
            if not resolution:
                markets_skipped_no_res += 1
                print(f"  {progress} SKIP (pas de resolution) {slug[:45]}", flush=True)
                continue

            # Verifier si deja indexe
            if slug:
                c.execute("SELECT COUNT(*) FROM wallet_trades WHERE market_slug=? AND market_resolved=1", (slug,))
                if c.fetchone()[0] > 0:
                    markets_skipped_dup += 1
                    print(f"  {progress} SKIP (deja indexe) {slug[:45]}", flush=True)
                    continue

            # Collecter les trades
            print(f"  {progress} {slug[:45]} (res={resolution})...", flush=True)
            trades = self.chain.collect_all_trades_for_market(market, verbose=True)
            if not trades:
                markets_skipped_no_trades += 1
                print(f"    -> 0 trades trouves", flush=True)
                continue

            markets_with_trades += 1
            source = trades[0].get("_source", "clob") if trades else "?"
            print(f"    -> {len(trades)} trades (source: {source})", flush=True)

            # Parser end_date pour calculer le timing
            end_date = None
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            for trade in trades:
                wallet = trade.get("maker_address", trade.get("maker", trade.get("taker_address", trade.get("taker", ""))))
                if not wallet:
                    continue
                wallet = wallet.lower()

                try:
                    price = float(trade.get("price", 0))
                    size = float(trade.get("size", trade.get("amount", 0)))
                except (ValueError, TypeError):
                    continue

                if size <= 0:
                    continue

                outcome_bought = trade.get("_outcome", trade.get("side", ""))
                role = trade.get("_role", "unknown")
                price_known = price > 0

                # Si prix inconnu (trades on-chain), estimer a 0.50
                if not price_known:
                    price = 0.50

                total_cost = price * size

                # Determiner si le trade a gagne
                won = False
                pnl = -total_cost  # Par defaut on perd le cout

                if outcome_bought.upper() == resolution:
                    won = True
                    pnl = (1.0 - price) * size  # Profit = (payout - prix) * taille

                # Timing avant resolution
                timing_min = 0
                trade_ts_str = trade.get("timestamp", trade.get("match_time", trade.get("created_at", "")))
                if trade_ts_str and end_date:
                    try:
                        if isinstance(trade_ts_str, (int, float)):
                            trade_ts = datetime.utcfromtimestamp(trade_ts_str)
                        else:
                            trade_ts = datetime.fromisoformat(str(trade_ts_str).replace("Z", "+00:00"))
                        diff = end_date - trade_ts
                        timing_min = max(0, diff.total_seconds() / 60)
                    except (ValueError, TypeError):
                        pass

                # Inserer le trade
                c.execute("""
                    INSERT INTO wallet_trades (
                        wallet_address, timestamp, market_slug, market_question,
                        condition_id, token_id, side, outcome_bought, price, size,
                        total_cost, market_resolved, resolution, trade_won, pnl,
                        time_before_resolution_min, role, raw_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    wallet, trade_ts_str or datetime.utcnow().isoformat(),
                    slug, question, condition_id,
                    trade.get("_token_id", ""), trade.get("side", ""),
                    outcome_bought, price, size, total_cost,
                    1, resolution, 1 if won else 0, pnl,
                    timing_min, role, json.dumps(trade),
                ))

                # Aggreger pour update wallet
                wallet_updates[wallet]["trades"] += 1
                wallet_updates[wallet]["volume"] += total_cost
                wallet_updates[wallet]["pnl"] += pnl
                wallet_updates[wallet]["markets"].add(slug)
                if won:
                    wallet_updates[wallet]["wins"] += 1
                else:
                    wallet_updates[wallet]["losses"] += 1

                indexed_count += 1

        # Update les profils wallet
        now = datetime.utcnow().isoformat()
        for address, stats in wallet_updates.items():
            total = stats["wins"] + stats["losses"]
            win_rate = stats["wins"] / total if total > 0 else 0
            num_markets = len(stats["markets"])

            c.execute("""
                INSERT INTO wallets (address, first_seen, last_seen, total_trades, total_wins,
                    total_losses, win_rate, total_volume, estimated_pnl, markets_traded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    last_seen = ?,
                    total_trades = total_trades + ?,
                    total_wins = total_wins + ?,
                    total_losses = total_losses + ?,
                    win_rate = CAST((total_wins + ?) AS REAL) / MAX(1, total_trades + ?),
                    total_volume = total_volume + ?,
                    estimated_pnl = estimated_pnl + ?,
                    markets_traded = markets_traded + ?
            """, (
                address, now, now, total, stats["wins"], stats["losses"],
                win_rate, stats["volume"], stats["pnl"], num_markets,
                now, total, stats["wins"], stats["losses"],
                stats["wins"], total, stats["volume"], stats["pnl"], num_markets,
            ))

        conn.commit()
        conn.close()

        print(f"\n  ╔══════════════════════════════════════╗", flush=True)
        print(f"  ║  RESUME INDEXATION                    ║", flush=True)
        print(f"  ╠══════════════════════════════════════╣", flush=True)
        print(f"  ║  Resolus scannes:   {len(resolved):>5}              ║", flush=True)
        print(f"  ║  Avec trades:       {markets_with_trades:>5}              ║", flush=True)
        print(f"  ║  Trades indexes:    {indexed_count:>5}              ║", flush=True)
        print(f"  ║  Wallets trouves:   {len(wallet_updates):>5}              ║", flush=True)
        print(f"  ║  Skip (pas de res): {markets_skipped_no_res:>5}              ║", flush=True)
        print(f"  ║  Skip (deja fait):  {markets_skipped_dup:>5}              ║", flush=True)
        print(f"  ║  Skip (0 trades):   {markets_skipped_no_trades:>5}              ║", flush=True)
        print(f"  ╚══════════════════════════════════════╝", flush=True)

        return {
            "markets_indexed": len(resolved) - markets_skipped_no_res - markets_skipped_dup - markets_skipped_no_trades if indexed_count > 0 else 0,
            "trades_indexed": indexed_count,
            "wallets_updated": len(wallet_updates),
        }

    # =========================================================================
    # SCORING - Calculer les scores de chaque wallet
    # =========================================================================

    def score_all_wallets(self, min_trades=5):
        """
        Calcule les scores pour tous les wallets avec assez de trades.

        Metriques :
        - Win rate brut et pondere par volume
        - PnL total et moyen par trade
        - Sharpe ratio (rendement ajuste au risque)
        - Timing score (combien de temps avant la resolution)
        - Consistency score (regularite des gains)
        - Volume score (taille des positions)
        - Insider score (composite de timing + win rate sur gros trades)
        """
        conn = self._conn()
        c = conn.cursor()
        now = datetime.utcnow().isoformat()

        # Recuperer tous les wallets avec min_trades
        c.execute("""
            SELECT address FROM wallets WHERE total_trades >= ?
        """, (min_trades,))
        addresses = [row[0] for row in c.fetchall()]

        scored = 0
        for address in addresses:
            score = self._compute_wallet_score(c, address)
            if not score:
                continue

            c.execute("""
                INSERT INTO wallet_scores (
                    address, timestamp, win_rate, win_rate_weighted,
                    avg_pnl_per_trade, total_pnl, sharpe_ratio,
                    avg_timing_score, consistency_score, volume_score,
                    insider_score, composite_score, tier
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    timestamp=?, win_rate=?, win_rate_weighted=?,
                    avg_pnl_per_trade=?, total_pnl=?, sharpe_ratio=?,
                    avg_timing_score=?, consistency_score=?, volume_score=?,
                    insider_score=?, composite_score=?, tier=?
            """, (
                address, now, score["win_rate"], score["win_rate_weighted"],
                score["avg_pnl"], score["total_pnl"], score["sharpe"],
                score["timing_score"], score["consistency"], score["volume_score"],
                score["insider_score"], score["composite"], score["tier"],
                # ON CONFLICT values
                now, score["win_rate"], score["win_rate_weighted"],
                score["avg_pnl"], score["total_pnl"], score["sharpe"],
                score["timing_score"], score["consistency"], score["volume_score"],
                score["insider_score"], score["composite"], score["tier"],
            ))

            # Update wallet is_smart flag
            is_smart = 1 if score["composite"] >= 70 else 0
            c.execute("""
                UPDATE wallets SET is_smart=?, smart_score=? WHERE address=?
            """, (is_smart, score["composite"], address))

            scored += 1

        # Rank
        c.execute("""
            SELECT address, composite_score FROM wallet_scores
            ORDER BY composite_score DESC
        """)
        for rank, (addr, _) in enumerate(c.fetchall(), 1):
            c.execute("UPDATE wallet_scores SET rank=? WHERE address=?", (rank, addr))

        conn.commit()
        conn.close()
        return scored

    def _compute_wallet_score(self, cursor, address):
        """
        Calcule le score detaille d'un wallet.

        Le score penalise les wallets avec peu de marches differents
        (un mec qui gagne 100% sur 2 marches correles c'est pas un insider).
        Un vrai smart trader a un bon win rate sur BEAUCOUP de marches differents.
        """
        cursor.execute("""
            SELECT price, size, total_cost, trade_won, pnl,
                   time_before_resolution_min, market_slug
            FROM wallet_trades
            WHERE wallet_address=? AND market_resolved=1
            ORDER BY timestamp ASC
        """, (address,))
        trades = cursor.fetchall()

        if len(trades) < 3:
            return None

        wins = sum(1 for t in trades if t[3] == 1)
        total = len(trades)
        win_rate = wins / total

        # PnL
        pnls = [t[4] for t in trades]
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / total

        # Win rate ponderee par volume (ratio du volume gagnant vs volume total)
        winning_volume = sum(t[2] for t in trades if t[3] == 1)
        total_volume = sum(t[2] for t in trades)
        win_rate_weighted = winning_volume / total_volume if total_volume > 0 else 0

        # Sharpe ratio
        if len(pnls) > 1:
            pnl_std = statistics.stdev(pnls)
            sharpe = (avg_pnl / pnl_std) if pnl_std > 0 else 0
        else:
            sharpe = 0

        # Timing score
        timings_wins = [t[5] for t in trades if t[3] == 1 and t[5] > 0]
        if timings_wins:
            avg_timing = statistics.mean(timings_wins)
            if avg_timing < 60:
                timing_score = 100
            elif avg_timing < 360:
                timing_score = 70
            elif avg_timing < 1440:
                timing_score = 40
            else:
                timing_score = 20
        else:
            avg_timing = 0
            timing_score = 0

        # Marches differents (diversite)
        markets = defaultdict(lambda: {"wins": 0, "total": 0})
        for t in trades:
            slug = t[6]
            markets[slug]["total"] += 1
            if t[3] == 1:
                markets[slug]["wins"] += 1

        num_markets = len(markets)

        # Consistency : ecart-type du win rate par marche
        market_win_rates = [m["wins"] / m["total"] for m in markets.values() if m["total"] > 0]
        if len(market_win_rates) > 1:
            consistency = max(0, 100 - statistics.stdev(market_win_rates) * 100)
        else:
            consistency = 30  # Un seul marche = faible confiance

        # Volume score (log scale)
        import math
        volume_score = min(100, math.log10(max(1, total_volume)) * 20)

        # Diversite score : combien de marches differents
        # C'est le filtre anti "100% sur 2 marches correles"
        # 1 marche = 10, 3 = 30, 5 = 50, 10 = 75, 20+ = 100
        if num_markets >= 20:
            diversity_score = 100
        elif num_markets >= 10:
            diversity_score = 75
        elif num_markets >= 5:
            diversity_score = 50
        elif num_markets >= 3:
            diversity_score = 30
        else:
            diversity_score = 10

        # Insider score : combine timing + win rate + gros trades
        insider_score = 0
        if win_rate >= 0.85 and total >= 10 and num_markets >= 3:
            insider_score += 40
        elif win_rate >= 0.70 and total >= 10 and num_markets >= 3:
            insider_score += 25
        elif win_rate >= 0.60 and num_markets >= 2:
            insider_score += 10

        if timing_score >= 70:
            insider_score += 30
        elif timing_score >= 40:
            insider_score += 15

        if total_volume > 10000:
            insider_score += 20
        elif total_volume > 1000:
            insider_score += 10

        if avg_pnl > 0:
            insider_score += 10

        insider_score = min(100, insider_score)

        # Composite score avec diversite
        # Poids : win_rate 20%, sharpe 10%, timing 15%, consistency 10%,
        #         volume 10%, insider 20%, diversite 15%
        composite = (
            win_rate * 20 +
            min(1, sharpe / 3) * 10 +
            (timing_score / 100) * 15 +
            (consistency / 100) * 10 +
            (volume_score / 100) * 10 +
            (insider_score / 100) * 20 +
            (diversity_score / 100) * 15
        )
        composite = min(100, composite)

        # Tier
        if composite >= 85:
            tier = "S - ELITE"
        elif composite >= 70:
            tier = "A - SMART MONEY"
        elif composite >= 55:
            tier = "B - ABOVE AVERAGE"
        elif composite >= 40:
            tier = "C - AVERAGE"
        else:
            tier = "D - BELOW AVERAGE"

        return {
            "win_rate": round(win_rate, 4),
            "win_rate_weighted": round(win_rate_weighted, 4),
            "avg_pnl": round(avg_pnl, 4),
            "total_pnl": round(total_pnl, 2),
            "sharpe": round(sharpe, 4),
            "timing_score": round(timing_score, 1),
            "avg_timing_min": round(avg_timing, 1),
            "consistency": round(consistency, 1),
            "volume_score": round(volume_score, 1),
            "insider_score": round(insider_score, 1),
            "diversity_score": round(diversity_score, 1),
            "num_markets": num_markets,
            "composite": round(composite, 1),
            "tier": tier,
            "total_trades": total,
            "total_volume": round(total_volume, 2),
        }

    # =========================================================================
    # QUERIES - Leaderboard, profil wallet, etc.
    # =========================================================================

    def get_leaderboard(self, limit=50, min_trades=10, sort_by="composite_score"):
        """Retourne le leaderboard des meilleurs wallets."""
        conn = self._conn()
        c = conn.cursor()

        valid_sorts = ["composite_score", "win_rate", "total_pnl", "insider_score", "sharpe_ratio"]
        if sort_by not in valid_sorts:
            sort_by = "composite_score"

        c.execute(f"""
            SELECT w.address, w.total_trades, w.total_wins, w.total_losses,
                   w.win_rate, w.total_volume, w.estimated_pnl,
                   ws.composite_score, ws.insider_score, ws.tier,
                   ws.sharpe_ratio, ws.avg_timing_score, ws.rank,
                   w.markets_traded
            FROM wallets w
            JOIN wallet_scores ws ON w.address = ws.address
            WHERE w.total_trades >= ?
            ORDER BY ws.{sort_by} DESC
            LIMIT ?
        """, (min_trades, limit))

        rows = c.fetchall()
        conn.close()

        return [
            {
                "address": r[0],
                "total_trades": r[1],
                "wins": r[2],
                "losses": r[3],
                "win_rate": r[4],
                "total_volume": r[5],
                "estimated_pnl": r[6],
                "composite_score": r[7],
                "insider_score": r[8],
                "tier": r[9],
                "sharpe": r[10],
                "timing_score": r[11],
                "rank": r[12],
                "markets_traded": r[13] or 0,
            }
            for r in rows
        ]

    def get_wallet_profile(self, address):
        """Profil detaille d'un wallet."""
        address = address.lower()
        conn = self._conn()
        c = conn.cursor()

        # Info de base
        c.execute("SELECT * FROM wallets WHERE address=?", (address,))
        wallet_row = c.fetchone()
        if not wallet_row:
            conn.close()
            return None

        cols = [desc[0] for desc in c.description]
        wallet = dict(zip(cols, wallet_row))

        # Score
        c.execute("SELECT * FROM wallet_scores WHERE address=?", (address,))
        score_row = c.fetchone()
        scores = {}
        if score_row:
            score_cols = [desc[0] for desc in c.description]
            scores = dict(zip(score_cols, score_row))

        # Trades recents
        c.execute("""
            SELECT timestamp, market_slug, market_question, outcome_bought,
                   price, size, total_cost, trade_won, pnl,
                   time_before_resolution_min, resolution
            FROM wallet_trades
            WHERE wallet_address=?
            ORDER BY timestamp DESC
            LIMIT 50
        """, (address,))
        trade_rows = c.fetchall()
        trades = [
            {
                "timestamp": t[0],
                "slug": t[1],
                "question": t[2],
                "outcome_bought": t[3],
                "price": t[4],
                "size": t[5],
                "total_cost": t[6],
                "won": bool(t[7]),
                "pnl": t[8],
                "timing_min": t[9],
                "resolution": t[10],
            }
            for t in trade_rows
        ]

        # Marches preferes
        c.execute("""
            SELECT market_slug, market_question,
                   COUNT(*) as trades,
                   SUM(trade_won) as wins,
                   SUM(pnl) as pnl
            FROM wallet_trades
            WHERE wallet_address=?
            GROUP BY market_slug
            ORDER BY trades DESC
            LIMIT 10
        """, (address,))
        fav_markets = [
            {
                "slug": r[0],
                "question": r[1],
                "trades": r[2],
                "wins": r[3],
                "pnl": round(r[4], 2) if r[4] else 0,
            }
            for r in c.fetchall()
        ]

        # Distribution des PnL
        c.execute("""
            SELECT pnl FROM wallet_trades
            WHERE wallet_address=? AND market_resolved=1
        """, (address,))
        all_pnls = [r[0] for r in c.fetchall() if r[0] is not None]

        pnl_stats = {}
        if all_pnls:
            pnl_stats = {
                "mean": round(statistics.mean(all_pnls), 4),
                "median": round(statistics.median(all_pnls), 4),
                "stdev": round(statistics.stdev(all_pnls), 4) if len(all_pnls) > 1 else 0,
                "max_win": round(max(all_pnls), 4),
                "max_loss": round(min(all_pnls), 4),
                "positive_pct": round(sum(1 for p in all_pnls if p > 0) / len(all_pnls) * 100, 1),
            }

        conn.close()

        return {
            "wallet": wallet,
            "scores": scores,
            "recent_trades": trades,
            "favorite_markets": fav_markets,
            "pnl_distribution": pnl_stats,
        }

    def get_smart_wallets(self, min_score=70):
        """Retourne les wallets identifies comme 'smart money'."""
        conn = self._conn()
        c = conn.cursor()
        c.execute("""
            SELECT w.address, w.total_trades, w.win_rate, w.estimated_pnl,
                   w.total_volume, ws.composite_score, ws.insider_score, ws.tier
            FROM wallets w
            JOIN wallet_scores ws ON w.address = ws.address
            WHERE ws.composite_score >= ?
            ORDER BY ws.composite_score DESC
        """, (min_score,))
        rows = c.fetchall()
        conn.close()

        return [
            {
                "address": r[0],
                "trades": r[1],
                "win_rate": r[2],
                "pnl": r[3],
                "volume": r[4],
                "score": r[5],
                "insider_score": r[6],
                "tier": r[7],
            }
            for r in rows
        ]

    # =========================================================================
    # WATCHLIST - Suivi manuel de wallets
    # =========================================================================

    def add_to_watchlist(self, address, reason="", priority=1, auto_copy=False):
        """Ajoute un wallet a la watchlist."""
        conn = self._conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO tracked_wallets (address, added_at, reason, priority, auto_copy)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                reason=?, priority=?, auto_copy=?
        """, (
            address.lower(), datetime.utcnow().isoformat(), reason, priority,
            1 if auto_copy else 0,
            reason, priority, 1 if auto_copy else 0,
        ))
        conn.commit()
        conn.close()

    def remove_from_watchlist(self, address):
        """Retire un wallet de la watchlist."""
        conn = self._conn()
        c = conn.cursor()
        c.execute("DELETE FROM tracked_wallets WHERE address=?", (address.lower(),))
        conn.commit()
        conn.close()

    def get_watchlist(self):
        """Retourne la watchlist."""
        conn = self._conn()
        c = conn.cursor()
        c.execute("""
            SELECT tw.address, tw.added_at, tw.reason, tw.priority, tw.auto_copy,
                   w.total_trades, w.win_rate, w.estimated_pnl,
                   ws.composite_score, ws.tier
            FROM tracked_wallets tw
            LEFT JOIN wallets w ON tw.address = w.address
            LEFT JOIN wallet_scores ws ON tw.address = ws.address
            ORDER BY tw.priority DESC, ws.composite_score DESC
        """)
        rows = c.fetchall()
        conn.close()

        return [
            {
                "address": r[0],
                "added_at": r[1],
                "reason": r[2],
                "priority": r[3],
                "auto_copy": bool(r[4]),
                "trades": r[5] or 0,
                "win_rate": r[6] or 0,
                "pnl": r[7] or 0,
                "score": r[8] or 0,
                "tier": r[9] or "UNKNOWN",
            }
            for r in rows
        ]

    # =========================================================================
    # STATS
    # =========================================================================

    def get_tracker_stats(self):
        """Stats du systeme de tracking."""
        conn = self._conn()
        c = conn.cursor()

        stats = {}
        c.execute("SELECT COUNT(*) FROM wallets")
        stats["total_wallets"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM wallets WHERE is_smart=1")
        stats["smart_wallets"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM wallet_trades")
        stats["total_trades_indexed"] = c.fetchone()[0]

        c.execute("SELECT COUNT(DISTINCT market_slug) FROM wallet_trades")
        stats["markets_indexed"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM tracked_wallets")
        stats["watchlist_size"] = c.fetchone()[0]

        c.execute("SELECT SUM(total_volume) FROM wallets")
        total_vol = c.fetchone()[0]
        stats["total_volume_tracked"] = round(total_vol, 2) if total_vol else 0

        conn.close()
        return stats
