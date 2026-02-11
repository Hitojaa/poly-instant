"""Collecteur de donnees en temps reel avec historique SQLite."""

import sqlite3
import time
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from .client import PolymarketClient

# Chemin de la DB
DB_PATH = Path(__file__).parent.parent / "data" / "polymarket.db"


class DataCollector:
    """
    Collecte et stocke les donnees de marche en continu.

    Stocke dans SQLite :
    - Snapshots de prix toutes les X secondes
    - Snapshots d'orderbook
    - Volume tracking
    - Evenements detectes (whales, momentum shifts, etc.)
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.client = PolymarketClient()
        self._ensure_db()

    def _ensure_db(self):
        """Cree la DB et les tables si necessaire."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                slug TEXT NOT NULL,
                question TEXT,
                yes_price REAL,
                no_price REAL,
                volume_24h REAL,
                liquidity REAL,
                total_cost REAL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                slug TEXT NOT NULL,
                side TEXT NOT NULL,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                bid_depth REAL,
                ask_depth REAL,
                num_bids INTEGER,
                num_asks INTEGER
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                slug TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT,
                data TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS market_meta (
                slug TEXT PRIMARY KEY,
                question TEXT,
                end_date TEXT,
                first_seen TEXT,
                last_updated TEXT,
                condition_id TEXT,
                yes_token_id TEXT,
                no_token_id TEXT
            )
        """)

        # Index pour les requetes rapides
        c.execute("CREATE INDEX IF NOT EXISTS idx_price_slug_ts ON price_snapshots(slug, timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_slug ON events(slug, timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_book_slug_ts ON orderbook_snapshots(slug, timestamp)")

        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    # -------------------------------------------------------------------------
    # COLLECT
    # -------------------------------------------------------------------------

    def collect_snapshot(self, limit=100):
        """
        Collecte un snapshot complet de tous les marches actifs.
        Retourne le nombre de marches enregistres.
        """
        markets = self.client.get_markets(limit=limit)
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        c = conn.cursor()
        count = 0

        for market in markets:
            slug = market.get("slug", "")
            if not slug:
                continue

            try:
                from .client import PolymarketClient as _PC
                yes_price, no_price, yes_token_id, no_token_id = _PC.extract_prices(market)
                if yes_price is None:
                    continue

                volume = float(market.get("volume24hr", 0) or market.get("volume", 0) or 0)
                liquidity = float(market.get("liquidity", 0) or 0)

                # Price snapshot
                c.execute("""
                    INSERT INTO price_snapshots (timestamp, slug, question, yes_price, no_price, volume_24h, liquidity, total_cost)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (now, slug, market.get("question", ""), yes_price, no_price, volume, liquidity, yes_price + no_price))

                # Update meta
                c.execute("""
                    INSERT INTO market_meta (slug, question, end_date, first_seen, last_updated, condition_id, yes_token_id, no_token_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        last_updated=?, question=?
                """, (
                    slug, market.get("question", ""), market.get("endDate", market.get("end_date", "")),
                    now, now, market.get("conditionId", market.get("condition_id", "")),
                    yes_token_id, no_token_id,
                    now, market.get("question", ""),
                ))

                count += 1
            except (ValueError, TypeError):
                continue

        conn.commit()
        conn.close()
        return count

    @staticmethod
    def _extract_prices(market):
        """
        Extrait yes_price, no_price et token IDs depuis un objet marche.
        Gere les differents formats de l'API Gamma :
        - Format tokens: [{"outcome":"Yes","price":"0.65","token_id":"xxx"}, ...]
        - Format outcomePrices: "0.65,0.35" avec outcomes: "Yes,No"
        - Format clobTokenIds: "id1,id2"
        """
        yes_price = None
        no_price = None
        yes_token_id = ""
        no_token_id = ""

        # Methode 1 : tokens array (format classique)
        tokens = market.get("tokens", [])
        if isinstance(tokens, list) and len(tokens) >= 2:
            yes_price = float(tokens[0].get("price", 0))
            no_price = float(tokens[1].get("price", 0))
            yes_token_id = tokens[0].get("token_id", "")
            no_token_id = tokens[1].get("token_id", "")
            return yes_price, no_price, yes_token_id, no_token_id

        # Methode 2 : outcomePrices string (format events)
        outcome_prices = market.get("outcomePrices", "")
        if outcome_prices:
            if isinstance(outcome_prices, str):
                parts = outcome_prices.replace("[", "").replace("]", "").replace('"', '').split(",")
            elif isinstance(outcome_prices, list):
                parts = outcome_prices
            else:
                parts = []

            if len(parts) >= 2:
                yes_price = float(parts[0].strip())
                no_price = float(parts[1].strip())

        # Methode 3 : champs best_bid/best_ask ou price directement
        if yes_price is None:
            if market.get("bestBid") is not None:
                yes_price = float(market.get("bestBid", 0))
                no_price = 1.0 - yes_price

        # Token IDs
        clob_ids = market.get("clobTokenIds", "")
        if clob_ids:
            if isinstance(clob_ids, str):
                id_parts = clob_ids.replace("[", "").replace("]", "").replace('"', '').split(",")
            elif isinstance(clob_ids, list):
                id_parts = clob_ids
            else:
                id_parts = []
            if len(id_parts) >= 2:
                yes_token_id = id_parts[0].strip()
                no_token_id = id_parts[1].strip()

        if yes_price is not None and no_price is not None:
            return yes_price, no_price, yes_token_id, no_token_id

        return None, None, "", ""

    def collect_orderbook(self, slug):
        """Collecte un snapshot d'orderbook pour un marche specifique."""
        conn = self._conn()
        c = conn.cursor()
        now = datetime.utcnow().isoformat()

        # Recuperer les token IDs
        c.execute("SELECT yes_token_id, no_token_id FROM market_meta WHERE slug=?", (slug,))
        row = c.fetchone()
        if not row:
            conn.close()
            return False

        yes_token_id, no_token_id = row

        for side, token_id in [("YES", yes_token_id), ("NO", no_token_id)]:
            if not token_id:
                continue
            try:
                book = self.client.get_orderbook(token_id)
                bids = book.get("bids", [])
                asks = book.get("asks", [])

                best_bid = float(bids[0]["price"]) if bids else 0
                best_ask = float(asks[0]["price"]) if asks else 0
                bid_depth = sum(float(b.get("size", 0)) for b in bids[:10])
                ask_depth = sum(float(a.get("size", 0)) for a in asks[:10])

                c.execute("""
                    INSERT INTO orderbook_snapshots (timestamp, slug, side, best_bid, best_ask, spread, bid_depth, ask_depth, num_bids, num_asks)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (now, slug, side, best_bid, best_ask,
                      best_ask - best_bid if best_bid and best_ask else 0,
                      bid_depth, ask_depth, len(bids), len(asks)))
            except Exception:
                continue

        conn.commit()
        conn.close()
        return True

    def log_event(self, slug, event_type, severity, message, data=None):
        """Enregistre un evenement detecte."""
        conn = self._conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO events (timestamp, slug, event_type, severity, message, data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.utcnow().isoformat(), slug, event_type, severity, message,
              json.dumps(data) if data else None))
        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # QUERY - Historique
    # -------------------------------------------------------------------------

    def get_price_history(self, slug, hours=24):
        """Recupere l'historique des prix pour un marche."""
        conn = self._conn()
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        c = conn.cursor()
        c.execute("""
            SELECT timestamp, yes_price, no_price, volume_24h, liquidity, total_cost
            FROM price_snapshots
            WHERE slug=? AND timestamp > ?
            ORDER BY timestamp ASC
        """, (slug, since))
        rows = c.fetchall()
        conn.close()

        return [
            {
                "timestamp": r[0],
                "yes_price": r[1],
                "no_price": r[2],
                "volume_24h": r[3],
                "liquidity": r[4],
                "total_cost": r[5],
            }
            for r in rows
        ]

    def get_price_snapshots_for_momentum(self, slug, hours=24):
        """Retourne les snapshots sous forme de tuples (timestamp, price) pour le momentum."""
        history = self.get_price_history(slug, hours)
        return [(h["timestamp"], h["yes_price"]) for h in history]

    def get_recent_events(self, slug=None, hours=24, event_type=None):
        """Recupere les evenements recents."""
        conn = self._conn()
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

        query = "SELECT timestamp, slug, event_type, severity, message, data FROM events WHERE timestamp > ?"
        params = [since]

        if slug:
            query += " AND slug=?"
            params.append(slug)
        if event_type:
            query += " AND event_type=?"
            params.append(event_type)

        query += " ORDER BY timestamp DESC LIMIT 100"

        c = conn.cursor()
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()

        return [
            {
                "timestamp": r[0],
                "slug": r[1],
                "event_type": r[2],
                "severity": r[3],
                "message": r[4],
                "data": json.loads(r[5]) if r[5] else None,
            }
            for r in rows
        ]

    def get_tracked_markets(self):
        """Liste les marches suivis."""
        conn = self._conn()
        c = conn.cursor()
        c.execute("SELECT slug, question, end_date, first_seen, last_updated FROM market_meta ORDER BY last_updated DESC")
        rows = c.fetchall()
        conn.close()
        return [
            {"slug": r[0], "question": r[1], "end_date": r[2], "first_seen": r[3], "last_updated": r[4]}
            for r in rows
        ]

    def get_stats(self):
        """Stats sur la base de donnees."""
        conn = self._conn()
        c = conn.cursor()
        stats = {}
        for table in ["price_snapshots", "orderbook_snapshots", "events", "market_meta"]:
            c.execute(f"SELECT COUNT(*) FROM {table}")
            stats[table] = c.fetchone()[0]
        conn.close()
        return stats

    def cleanup_old_data(self, days=7):
        """Supprime les donnees de plus de X jours."""
        conn = self._conn()
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        c = conn.cursor()
        for table in ["price_snapshots", "orderbook_snapshots", "events"]:
            c.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
        conn.commit()
        conn.close()
