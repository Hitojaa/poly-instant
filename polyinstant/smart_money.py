"""Detection de smart money, clusters sybil, et activite pre-resolution."""

import sqlite3
import json
import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, Counter

from .blockchain import PolygonClient
from .client import PolymarketClient

DB_PATH = Path(__file__).parent.parent / "data" / "polymarket.db"


class SmartMoneyDetector:
    """
    Detecte les wallets "smart money" et les patterns suspects.

    Analyses :
    1. Pre-resolution activity : wallets qui achetent juste avant la bonne resolution
    2. Sybil cluster detection : groupes de wallets qui tradent en meme temps
    3. Abnormal timing patterns : timing statistiquement anormal
    4. Coordinated buying : achats coordonnes sur le meme outcome
    5. Wallet network mapping : liens entre wallets (funded from same source)
    """

    # Seuils de detection
    THRESHOLDS = {
        "pre_resolution_window_min": 120,   # Fenetre pre-resolution (2h)
        "suspicious_timing_min": 60,         # Timing suspect si < 60 min
        "min_win_rate_smart": 0.70,          # Win rate minimum pour smart money
        "min_trades_smart": 10,              # Nombre minimum de trades
        "sybil_time_window_sec": 300,        # Fenetre pour cluster (5 min)
        "sybil_min_wallets": 3,              # Minimum de wallets dans un cluster
        "large_trade_multiplier": 5,         # x fois la taille mediane = gros trade
    }

    def __init__(self, polygonscan_api_key="", db_path=None):
        self.db_path = db_path or DB_PATH
        self.poly_client = PolymarketClient()
        self.chain = PolygonClient(polygonscan_api_key)

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    # =========================================================================
    # PRE-RESOLUTION ACTIVITY ANALYSIS
    # =========================================================================

    def analyze_pre_resolution(self, market_slug=None, window_min=None):
        """
        Analyse l'activite juste avant la resolution d'un marche.

        Identifie les wallets qui ont achete le bon outcome
        dans les dernieres X minutes avant la resolution.

        C'est LA methode cle pour trouver les insiders/smart traders.
        """
        window = window_min or self.THRESHOLDS["pre_resolution_window_min"]
        conn = self._conn()
        c = conn.cursor()

        # Query : trades gagnants dans la fenetre pre-resolution
        query = """
            SELECT wallet_address, market_slug, market_question,
                   outcome_bought, price, size, total_cost, pnl,
                   time_before_resolution_min, resolution, timestamp
            FROM wallet_trades
            WHERE market_resolved = 1
              AND trade_won = 1
              AND time_before_resolution_min > 0
              AND time_before_resolution_min <= ?
        """
        params = [window]

        if market_slug:
            query += " AND market_slug = ?"
            params.append(market_slug)

        query += " ORDER BY time_before_resolution_min ASC"

        c.execute(query, params)
        trades = c.fetchall()
        conn.close()

        if not trades:
            return {"wallets": [], "patterns": [], "summary": {"total_trades": 0}}

        # Grouper par wallet
        wallet_activity = defaultdict(list)
        for t in trades:
            wallet_activity[t[0]].append({
                "slug": t[1],
                "question": t[2],
                "outcome": t[3],
                "price": t[4],
                "size": t[5],
                "cost": t[6],
                "pnl": t[7],
                "timing_min": t[8],
                "resolution": t[9],
                "timestamp": t[10],
            })

        # Scorer chaque wallet
        suspicious_wallets = []
        for address, activity in wallet_activity.items():
            score = self._score_pre_resolution(address, activity)
            if score["suspicion_score"] > 30:
                suspicious_wallets.append({
                    "address": address,
                    **score,
                    "trades": activity[:10],  # Top 10 trades
                })

        # Trier par score de suspicion
        suspicious_wallets.sort(key=lambda x: x["suspicion_score"], reverse=True)

        # Patterns detectes
        patterns = self._detect_patterns(trades)

        return {
            "wallets": suspicious_wallets[:50],
            "patterns": patterns,
            "summary": {
                "total_trades": len(trades),
                "unique_wallets": len(wallet_activity),
                "suspicious_wallets": len(suspicious_wallets),
                "window_minutes": window,
            },
        }

    def _score_pre_resolution(self, address, activity):
        """Score de suspicion pour un wallet base sur son activite pre-resolution."""
        total = len(activity)
        timings = [t["timing_min"] for t in activity]
        volumes = [t["cost"] for t in activity]
        pnls = [t["pnl"] for t in activity]

        avg_timing = statistics.mean(timings) if timings else 0
        total_pnl = sum(pnls)
        total_volume = sum(volumes)
        avg_price = statistics.mean([t["price"] for t in activity]) if activity else 0

        # Score de suspicion (0-100)
        score = 0

        # Timing : plus c'est proche de la resolution, plus c'est suspect
        if avg_timing < 15:
            score += 40  # < 15 min = tres suspect
        elif avg_timing < 30:
            score += 30
        elif avg_timing < 60:
            score += 20
        elif avg_timing < 120:
            score += 10

        # Repetition : beaucoup de trades pre-resolution = pattern
        if total >= 20:
            score += 25
        elif total >= 10:
            score += 15
        elif total >= 5:
            score += 10

        # Gros volumes
        if total_volume > 50000:
            score += 20
        elif total_volume > 10000:
            score += 15
        elif total_volume > 1000:
            score += 8

        # PnL positif constant
        if total_pnl > 10000:
            score += 15
        elif total_pnl > 1000:
            score += 10

        # Le wallet achete toujours a bas prix (sait que ca va monter)
        if avg_price < 0.40:
            score += 10
        elif avg_price < 0.50:
            score += 5

        score = min(100, score)

        # Label
        if score >= 80:
            label = "HIGHLY SUSPICIOUS - Probable insider"
        elif score >= 60:
            label = "SUSPICIOUS - Smart trader ou info avantage"
        elif score >= 40:
            label = "WORTH WATCHING - Pattern notable"
        else:
            label = "LOW SUSPICION - Probablement normal"

        return {
            "suspicion_score": score,
            "label": label,
            "total_pre_res_trades": total,
            "avg_timing_min": round(avg_timing, 1),
            "min_timing_min": round(min(timings), 1) if timings else 0,
            "total_volume": round(total_volume, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_entry_price": round(avg_price, 4),
            "markets_count": len(set(t["slug"] for t in activity)),
        }

    def _detect_patterns(self, trades):
        """Detecte des patterns dans les trades pre-resolution."""
        patterns = []

        # Pattern 1: Burst d'achats (beaucoup de trades dans une courte fenetre)
        by_market = defaultdict(list)
        for t in trades:
            by_market[t[1]].append(t)  # group by slug

        for slug, market_trades in by_market.items():
            if len(market_trades) >= 5:
                timings = [t[8] for t in market_trades]
                min_time = min(timings)
                max_time = max(timings)
                time_range = max_time - min_time

                if time_range < 30 and len(market_trades) >= 5:
                    patterns.append({
                        "type": "BURST_BUYING",
                        "slug": slug,
                        "question": market_trades[0][2],
                        "wallet_count": len(set(t[0] for t in market_trades)),
                        "trade_count": len(market_trades),
                        "time_range_min": round(time_range, 1),
                        "description": f"{len(market_trades)} trades en {time_range:.0f} min par {len(set(t[0] for t in market_trades))} wallets",
                    })

        # Pattern 2: Meme wallet, multiple marches, toujours gagnant
        wallet_wins = defaultdict(int)
        wallet_totals = defaultdict(int)
        for t in trades:
            wallet_totals[t[0]] += 1
            if t[7] > 0:  # pnl > 0
                wallet_wins[t[0]] += 1

        for address, total in wallet_totals.items():
            if total >= 5:
                wr = wallet_wins[address] / total
                if wr >= 0.90:
                    patterns.append({
                        "type": "SERIAL_WINNER",
                        "address": address,
                        "win_rate": round(wr * 100, 1),
                        "trades": total,
                        "description": f"Wallet {address[:10]}... : {wr*100:.0f}% win rate sur {total} trades pre-resolution",
                    })

        return patterns

    # =========================================================================
    # SYBIL / CLUSTER DETECTION
    # =========================================================================

    def detect_sybil_clusters(self, market_slug=None, time_window_sec=None):
        """
        Detecte les clusters de wallets qui tradent de maniere coordonnee.

        Un cluster sybil = plusieurs wallets qui achetent le meme outcome
        dans une fenetre de temps tres courte (< 5 min).

        C'est un signal fort que ces wallets sont controles par la meme personne.
        """
        window = time_window_sec or self.THRESHOLDS["sybil_time_window_sec"]
        min_wallets = self.THRESHOLDS["sybil_min_wallets"]

        conn = self._conn()
        c = conn.cursor()

        # Recuperer les trades
        query = """
            SELECT wallet_address, market_slug, market_question,
                   outcome_bought, price, size, total_cost, timestamp
            FROM wallet_trades
            WHERE market_resolved = 1
        """
        params = []
        if market_slug:
            query += " AND market_slug = ?"
            params.append(market_slug)

        query += " ORDER BY market_slug, timestamp ASC"
        c.execute(query, params)
        trades = c.fetchall()
        conn.close()

        if not trades:
            return {"clusters": [], "summary": {"total_clusters": 0}}

        # Grouper par marche et outcome
        groups = defaultdict(list)
        for t in trades:
            key = f"{t[1]}_{t[3]}"  # slug_outcome
            groups[key].append({
                "address": t[0],
                "slug": t[1],
                "question": t[2],
                "outcome": t[3],
                "price": t[4],
                "size": t[5],
                "cost": t[6],
                "timestamp": t[7],
            })

        clusters = []
        for key, group_trades in groups.items():
            if len(group_trades) < min_wallets:
                continue

            # Detecter les fenetres temporelles avec plusieurs wallets
            found_clusters = self._find_time_clusters(group_trades, window, min_wallets)
            clusters.extend(found_clusters)

        # Trier par taille de cluster
        clusters.sort(key=lambda x: x["wallet_count"], reverse=True)

        # Cross-cluster analysis : wallets qui apparaissent dans plusieurs clusters
        wallet_cluster_count = Counter()
        for cluster in clusters:
            for addr in cluster["wallets"]:
                wallet_cluster_count[addr] += 1

        repeat_offenders = [
            {"address": addr, "cluster_appearances": count}
            for addr, count in wallet_cluster_count.most_common(20)
            if count >= 2
        ]

        return {
            "clusters": clusters[:50],
            "repeat_offenders": repeat_offenders,
            "summary": {
                "total_clusters": len(clusters),
                "unique_wallets_in_clusters": len(wallet_cluster_count),
                "repeat_offenders": len(repeat_offenders),
            },
        }

    def _find_time_clusters(self, trades, window_sec, min_wallets):
        """Trouve les clusters de trades dans une fenetre temporelle."""
        clusters = []

        # Parser les timestamps
        parsed = []
        for t in trades:
            ts = t["timestamp"]
            try:
                if isinstance(ts, (int, float)):
                    dt = datetime.utcfromtimestamp(ts)
                else:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                parsed.append((dt, t))
            except (ValueError, TypeError):
                continue

        parsed.sort(key=lambda x: x[0])

        # Sliding window
        i = 0
        while i < len(parsed):
            window_trades = [parsed[i]]
            j = i + 1

            while j < len(parsed):
                diff = (parsed[j][0] - parsed[i][0]).total_seconds()
                if diff <= window_sec:
                    window_trades.append(parsed[j])
                    j += 1
                else:
                    break

            # Check si assez de wallets uniques
            unique_wallets = set(t[1]["address"] for t in window_trades)
            if len(unique_wallets) >= min_wallets:
                total_volume = sum(t[1]["cost"] for t in window_trades)
                clusters.append({
                    "market_slug": window_trades[0][1]["slug"],
                    "question": window_trades[0][1]["question"],
                    "outcome": window_trades[0][1]["outcome"],
                    "wallet_count": len(unique_wallets),
                    "trade_count": len(window_trades),
                    "wallets": list(unique_wallets),
                    "total_volume": round(total_volume, 2),
                    "time_span_sec": round((window_trades[-1][0] - window_trades[0][0]).total_seconds(), 1),
                    "start_time": window_trades[0][0].isoformat(),
                    "avg_price": round(statistics.mean(t[1]["price"] for t in window_trades), 4),
                    "description": (
                        f"{len(unique_wallets)} wallets achetent {window_trades[0][1]['outcome']} "
                        f"en {(window_trades[-1][0] - window_trades[0][0]).total_seconds():.0f}s"
                    ),
                })

            i = j if j > i + 1 else i + 1

        return clusters

    # =========================================================================
    # ABNORMAL ACTIVITY DETECTION
    # =========================================================================

    def detect_abnormal_activity(self, hours=24):
        """
        Detecte les activites anormales sur les marches recents.

        Signaux :
        - Gros trade inhabituel sur un marche calme
        - Spike soudain de volume
        - Wallet inconnu qui fait un gros trade
        - Plusieurs wallets qui achetent le meme outcome en meme temps
        """
        conn = self._conn()
        c = conn.cursor()
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

        c.execute("""
            SELECT wallet_address, market_slug, market_question,
                   outcome_bought, price, size, total_cost, timestamp,
                   trade_won, pnl
            FROM wallet_trades
            WHERE timestamp > ?
            ORDER BY timestamp DESC
        """, (since,))
        trades = c.fetchall()

        if not trades:
            conn.close()
            return {"alerts": [], "summary": {"total_trades": 0}}

        # Calculer les medianes pour detecter les outliers
        costs = [t[6] for t in trades if t[6] and t[6] > 0]
        median_cost = statistics.median(costs) if costs else 0
        large_threshold = median_cost * self.THRESHOLDS["large_trade_multiplier"]

        alerts = []

        # Detecter les gros trades
        for t in trades:
            cost = t[6] or 0
            if cost >= large_threshold and large_threshold > 0:
                # Verifier si le wallet est connu
                c.execute("SELECT total_trades, win_rate, is_smart FROM wallets WHERE address=?", (t[0],))
                wallet_info = c.fetchone()
                is_new = not wallet_info or wallet_info[0] < 3

                alert_type = "NEW_WHALE" if is_new else "LARGE_TRADE"
                severity = "HIGH" if is_new and cost > large_threshold * 2 else "MEDIUM"

                alerts.append({
                    "type": alert_type,
                    "severity": severity,
                    "wallet": t[0],
                    "slug": t[1],
                    "question": t[2],
                    "outcome": t[3],
                    "price": t[4],
                    "size": t[5],
                    "cost": round(cost, 2),
                    "timestamp": t[7],
                    "is_new_wallet": is_new,
                    "wallet_trades": wallet_info[0] if wallet_info else 0,
                    "wallet_win_rate": wallet_info[1] if wallet_info else 0,
                    "is_smart": bool(wallet_info[2]) if wallet_info else False,
                    "description": (
                        f"{'Nouveau wallet' if is_new else 'Wallet connu'} "
                        f"achete ${cost:,.0f} de {t[3]} sur {t[2][:40]}"
                    ),
                })

        conn.close()

        # Trier par severite et cout
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        alerts.sort(key=lambda x: (severity_order.get(x["severity"], 3), -x["cost"]))

        return {
            "alerts": alerts[:50],
            "summary": {
                "total_trades": len(trades),
                "large_trades": len([a for a in alerts if a["type"] == "LARGE_TRADE"]),
                "new_whales": len([a for a in alerts if a["type"] == "NEW_WHALE"]),
                "median_trade_size": round(median_cost, 2),
                "large_threshold": round(large_threshold, 2),
            },
        }

    # =========================================================================
    # WALLET NETWORK ANALYSIS
    # =========================================================================

    def analyze_wallet_network(self, address, depth=1):
        """
        Analyse le reseau d'un wallet :
        - D'ou viennent ses fonds (funding source)
        - Autres wallets finances par la meme source
        - Wallets qui tradent les memes marches au meme moment

        depth=1 : liens directs
        depth=2 : liens de 2e niveau (friends of friends)
        """
        address = address.lower()
        conn = self._conn()
        c = conn.cursor()

        # 1. Trouver les wallets qui tradent les memes marches au meme moment
        c.execute("""
            SELECT DISTINCT market_slug, timestamp, outcome_bought
            FROM wallet_trades
            WHERE wallet_address = ?
        """, (address,))
        target_trades = c.fetchall()

        co_traders = defaultdict(int)
        for slug, ts, outcome in target_trades:
            # Chercher d'autres wallets qui ont trade le meme marche et outcome
            c.execute("""
                SELECT wallet_address FROM wallet_trades
                WHERE market_slug = ?
                  AND outcome_bought = ?
                  AND wallet_address != ?
            """, (slug, outcome, address))

            for row in c.fetchall():
                co_traders[row[0]] += 1

        # Trier par nombre de co-trades
        related_wallets = [
            {"address": addr, "co_trades": count}
            for addr, count in sorted(co_traders.items(), key=lambda x: x[1], reverse=True)
            if count >= 2
        ][:20]

        # 2. Enrichir avec les scores
        enriched = []
        for rw in related_wallets:
            c.execute("""
                SELECT w.total_trades, w.win_rate, w.estimated_pnl, w.is_smart,
                       ws.composite_score, ws.tier
                FROM wallets w
                LEFT JOIN wallet_scores ws ON w.address = ws.address
                WHERE w.address = ?
            """, (rw["address"],))
            info = c.fetchone()

            enriched.append({
                **rw,
                "trades": info[0] if info else 0,
                "win_rate": info[1] if info else 0,
                "pnl": info[2] if info else 0,
                "is_smart": bool(info[3]) if info else False,
                "score": info[4] if info and info[4] else 0,
                "tier": info[5] if info and info[5] else "UNKNOWN",
            })

        # 3. Verifier les correlations de timing
        timing_correlations = []
        for rw in enriched[:10]:
            correlation = self._compute_timing_correlation(c, address, rw["address"])
            if correlation and correlation["correlation"] > 0.5:
                timing_correlations.append({
                    "address": rw["address"],
                    **correlation,
                })

        conn.close()

        return {
            "target": address,
            "related_wallets": enriched,
            "timing_correlations": timing_correlations,
            "network_size": len(enriched),
            "smart_connections": len([w for w in enriched if w["is_smart"]]),
        }

    def _compute_timing_correlation(self, cursor, addr1, addr2):
        """Calcule la correlation temporelle entre deux wallets."""
        # Recuperer les marches en commun
        cursor.execute("""
            SELECT DISTINCT a.market_slug
            FROM wallet_trades a
            JOIN wallet_trades b ON a.market_slug = b.market_slug
            WHERE a.wallet_address = ? AND b.wallet_address = ?
        """, (addr1, addr2))
        common_markets = [r[0] for r in cursor.fetchall()]

        if len(common_markets) < 2:
            return None

        # Pour chaque marche commun, comparer le timing
        time_diffs = []
        same_outcome_count = 0
        total_common = 0

        for slug in common_markets[:20]:
            cursor.execute("""
                SELECT outcome_bought, timestamp FROM wallet_trades
                WHERE wallet_address = ? AND market_slug = ?
                LIMIT 1
            """, (addr1, slug))
            t1 = cursor.fetchone()

            cursor.execute("""
                SELECT outcome_bought, timestamp FROM wallet_trades
                WHERE wallet_address = ? AND market_slug = ?
                LIMIT 1
            """, (addr2, slug))
            t2 = cursor.fetchone()

            if t1 and t2:
                total_common += 1
                if t1[0] == t2[0]:
                    same_outcome_count += 1

        if total_common == 0:
            return None

        same_outcome_rate = same_outcome_count / total_common

        return {
            "common_markets": total_common,
            "same_outcome_rate": round(same_outcome_rate, 3),
            "correlation": round(same_outcome_rate, 3),
            "likely_sybil": same_outcome_rate > 0.8 and total_common >= 5,
        }

    # =========================================================================
    # MARKET-SPECIFIC SMART MONEY ANALYSIS
    # =========================================================================

    def smart_money_flow(self, market_slug):
        """
        Analyse le flux de smart money sur un marche specifique.

        Qui achete quoi parmi les wallets les mieux scores ?
        """
        conn = self._conn()
        c = conn.cursor()

        c.execute("""
            SELECT wt.wallet_address, wt.outcome_bought, wt.price, wt.size,
                   wt.total_cost, wt.timestamp,
                   w.win_rate, w.estimated_pnl, w.total_trades,
                   ws.composite_score, ws.tier, ws.insider_score
            FROM wallet_trades wt
            JOIN wallets w ON wt.wallet_address = w.address
            LEFT JOIN wallet_scores ws ON wt.wallet_address = ws.address
            WHERE wt.market_slug = ?
              AND w.total_trades >= 5
            ORDER BY ws.composite_score DESC
        """, (market_slug,))
        rows = c.fetchall()
        conn.close()

        if not rows:
            return None

        # Separer smart money vs normal
        smart_trades = [r for r in rows if r[9] and r[9] >= 70]
        normal_trades = [r for r in rows if not r[9] or r[9] < 70]

        # Analyser la direction du smart money
        smart_yes = sum(r[4] for r in smart_trades if r[1] and r[1].upper() == "YES")
        smart_no = sum(r[4] for r in smart_trades if r[1] and r[1].upper() == "NO")
        normal_yes = sum(r[4] for r in normal_trades if r[1] and r[1].upper() == "YES")
        normal_no = sum(r[4] for r in normal_trades if r[1] and r[1].upper() == "NO")

        smart_total = smart_yes + smart_no
        smart_bias = "YES" if smart_yes > smart_no else ("NO" if smart_no > smart_yes else "NEUTRAL")
        smart_conviction = abs(smart_yes - smart_no) / smart_total if smart_total > 0 else 0

        return {
            "market": market_slug,
            "smart_money": {
                "wallets": len(set(r[0] for r in smart_trades)),
                "trades": len(smart_trades),
                "yes_volume": round(smart_yes, 2),
                "no_volume": round(smart_no, 2),
                "total_volume": round(smart_total, 2),
                "bias": smart_bias,
                "conviction": round(smart_conviction * 100, 1),
            },
            "normal_money": {
                "wallets": len(set(r[0] for r in normal_trades)),
                "trades": len(normal_trades),
                "yes_volume": round(normal_yes, 2),
                "no_volume": round(normal_no, 2),
            },
            "top_smart_traders": [
                {
                    "address": r[0],
                    "outcome": r[1],
                    "price": r[2],
                    "size": r[3],
                    "cost": round(r[4], 2),
                    "score": r[9],
                    "tier": r[10],
                    "win_rate": r[6],
                    "insider_score": r[11],
                }
                for r in smart_trades[:10]
            ],
            "signal": {
                "direction": smart_bias,
                "confidence": round(smart_conviction * 100, 1),
                "smart_volume_pct": round(smart_total / (smart_total + normal_yes + normal_no) * 100, 1) if (smart_total + normal_yes + normal_no) > 0 else 0,
            },
        }

    # =========================================================================
    # FULL SCAN
    # =========================================================================

    def full_intelligence_scan(self, limit=30):
        """
        Scan d'intelligence complet : combine toutes les analyses.

        Retourne un rapport unifie avec :
        - Smart wallets detectes
        - Clusters sybil
        - Activite pre-resolution suspecte
        - Activite anormale recente
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "pre_resolution": None,
            "sybil_clusters": None,
            "abnormal_activity": None,
            "summary": {},
        }

        # Pre-resolution analysis
        try:
            report["pre_resolution"] = self.analyze_pre_resolution()
        except Exception as e:
            report["pre_resolution"] = {"error": str(e)}

        # Sybil clusters
        try:
            report["sybil_clusters"] = self.detect_sybil_clusters()
        except Exception as e:
            report["sybil_clusters"] = {"error": str(e)}

        # Abnormal activity
        try:
            report["abnormal_activity"] = self.detect_abnormal_activity(hours=24)
        except Exception as e:
            report["abnormal_activity"] = {"error": str(e)}

        # Summary
        pre = report["pre_resolution"] or {}
        syb = report["sybil_clusters"] or {}
        abn = report["abnormal_activity"] or {}

        report["summary"] = {
            "suspicious_wallets": len(pre.get("wallets", [])),
            "sybil_clusters": len(syb.get("clusters", [])),
            "abnormal_alerts": len(abn.get("alerts", [])),
            "patterns_detected": len(pre.get("patterns", [])),
        }

        return report
