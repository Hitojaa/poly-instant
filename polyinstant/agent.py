"""Agent IA de recherche d'opportunites sur Polymarket.

Scan les marches actifs, recherche des infos sur le web,
et utilise Claude pour evaluer si les odds sont corrects.
"""

import os
import json
import time
from datetime import datetime

from .client import PolymarketClient


class MarketAgent:
    """
    Agent autonome qui scan Polymarket et recherche les meilleures opportunites.

    Pipeline :
    1. Fetch les marches actifs (filtre par volume/liquidite)
    2. Pour chaque marche interessant :
       a. Recherche web (news, Twitter/X)
       b. Analyse IA (Claude) : odds corrects ? edge ?
    3. Classement des opportunites
    4. Alerte Telegram (optionnel)
    """

    # Nombre max de resultats de recherche web par marche
    MAX_SEARCH_RESULTS = 5
    # Volume minimum pour considerer un marche (en $)
    MIN_VOLUME = 5000
    # Nombre max de marches a analyser par scan
    MAX_MARKETS_PER_SCAN = 20

    def __init__(self, anthropic_api_key=None, notifier=None):
        self.client = PolymarketClient()
        self.notifier = notifier

        # Anthropic SDK
        api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY requis. Ajoute-le dans .env ou passe-le en parametre.\n"
                "  → https://console.anthropic.com/settings/keys"
            )

        try:
            import anthropic
            self.llm = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError(
                "Le package 'anthropic' est requis.\n"
                "  → pip install anthropic"
            )

        # DuckDuckGo search
        try:
            from duckduckgo_search import DDGS
            self._ddgs = DDGS
        except ImportError:
            raise ImportError(
                "Le package 'duckduckgo-search' est requis.\n"
                "  → pip install duckduckgo-search"
            )

        self.model = "claude-sonnet-4-5-20250929"

    # =========================================================================
    # WEB SEARCH
    # =========================================================================

    def _web_search(self, query, max_results=5):
        """Recherche web via DuckDuckGo (gratuit, pas de cle API)."""
        try:
            with self._ddgs() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return [
                {"title": r.get("title", ""), "body": r.get("body", ""), "url": r.get("href", "")}
                for r in results
            ]
        except Exception as e:
            print(f"    [search] Erreur: {e}")
            return []

    def _twitter_search(self, query, max_results=5):
        """Recherche sur X/Twitter via DuckDuckGo."""
        return self._web_search(f"site:x.com OR site:twitter.com {query}", max_results=max_results)

    def _build_search_queries(self, question, description=""):
        """
        Genere les queries de recherche optimales pour un marche.
        Retourne une liste de queries a executer.
        """
        # Nettoyer la question
        q = question.strip().rstrip("?").strip()

        queries = [
            q,  # La question directe
            f"{q} latest news",  # News recentes
        ]

        return queries

    # =========================================================================
    # MARKET DISCOVERY
    # =========================================================================

    def discover_markets(self, limit=100, min_volume=None):
        """
        Recupere et filtre les marches actifs les plus interessants.

        Criteres de filtrage :
        - Volume > min_volume
        - Prix entre 0.05 et 0.95 (pas deja decide)
        - Marche actif et non archive
        """
        min_vol = min_volume or self.MIN_VOLUME

        print(f"  Recuperation des marches actifs...", flush=True)
        raw_markets = self.client.get_markets(limit=limit, active=True)

        candidates = []
        for m in raw_markets:
            # Extraire prix et volume
            yes_price, no_price, yes_tid, no_tid = self.client.extract_prices(m)
            if yes_price is None:
                continue

            volume = float(m.get("volume", 0) or m.get("volume24hr", 0) or 0)
            liquidity = float(m.get("liquidity", 0) or 0)
            question = m.get("question", m.get("title", ""))

            if not question:
                continue

            # Filtrer les marches deja decides (>95% ou <5%)
            if yes_price > 0.95 or yes_price < 0.05:
                continue

            # Filtrer par volume
            if volume < min_vol:
                continue

            candidates.append({
                "question": question,
                "slug": m.get("slug", ""),
                "description": m.get("description", ""),
                "yes_price": yes_price,
                "no_price": no_price,
                "volume": volume,
                "liquidity": liquidity,
                "end_date": m.get("endDate", m.get("end_date_iso", "")),
                "category": m.get("category", m.get("groupItemTitle", "")),
                "yes_token_id": yes_tid,
                "no_token_id": no_tid,
                "raw": m,
            })

        # Trier par volume decroissant
        candidates.sort(key=lambda x: x["volume"], reverse=True)

        print(f"  {len(candidates)} marches candidats (filtre: vol>${min_vol:,.0f}, prix 5-95%)")
        return candidates

    # =========================================================================
    # AI ANALYSIS
    # =========================================================================

    def _research_market(self, market):
        """
        Recherche web complete pour un marche.
        Retourne un contexte texte avec toutes les infos trouvees.
        """
        question = market["question"]
        search_queries = self._build_search_queries(question, market.get("description", ""))

        all_results = []
        seen_urls = set()

        for query in search_queries:
            results = self._web_search(query, max_results=self.MAX_SEARCH_RESULTS)
            for r in results:
                if r["url"] not in seen_urls:
                    all_results.append(r)
                    seen_urls.add(r["url"])

        # Recherche Twitter/X aussi
        twitter_results = self._twitter_search(question, max_results=3)
        for r in twitter_results:
            if r["url"] not in seen_urls:
                r["title"] = f"[X/Twitter] {r['title']}"
                all_results.append(r)
                seen_urls.add(r["url"])

        # Formater en contexte texte
        if not all_results:
            return "Aucun resultat de recherche web trouve."

        context_parts = []
        for i, r in enumerate(all_results[:10], 1):
            context_parts.append(f"[{i}] {r['title']}\n    {r['body']}\n    Source: {r['url']}")

        return "\n\n".join(context_parts)

    def _analyze_with_ai(self, market, research_context):
        """
        Utilise Claude pour analyser un marche avec le contexte de recherche.

        Retourne un dict avec :
        - estimated_probability: probabilite estimee par l'IA (0-1)
        - confidence: niveau de confiance de l'IA (0-100)
        - edge: ecart entre le prix du marche et la proba estimee
        - direction: BUY_YES, BUY_NO, ou SKIP
        - reasoning: explication en francais
        - risk_level: LOW, MEDIUM, HIGH
        """
        question = market["question"]
        yes_price = market["yes_price"]
        volume = market["volume"]
        end_date = market.get("end_date", "")

        prompt = f"""Tu es un analyste expert en marches predictifs (Polymarket).

MARCHE A ANALYSER :
- Question : {question}
- Prix actuel YES : {yes_price:.1%} (= le marche pense que ca a {yes_price:.0%} de chances)
- Prix actuel NO : {market['no_price']:.1%}
- Volume : ${volume:,.0f}
- Date de fin : {end_date or 'Non specifiee'}
- Description : {market.get('description', 'N/A')[:500]}

RESULTATS DE RECHERCHE WEB (informations fraiches) :
{research_context}

INSTRUCTIONS :
Analyse les informations de recherche web et determine si le prix actuel du marche est correct, trop haut ou trop bas.

Reponds UNIQUEMENT avec un JSON valide (pas de markdown, pas de texte autour) :
{{
    "estimated_probability": <float 0.0-1.0, ta proba estimee que YES se realise>,
    "confidence": <int 0-100, ta confiance dans ton estimation>,
    "direction": "<BUY_YES si le marche sous-evalue YES, BUY_NO si sur-evalue, SKIP si correct>",
    "edge_pct": <float, ecart en points de pourcentage entre ton estimation et le prix>,
    "risk_level": "<LOW|MEDIUM|HIGH>",
    "reasoning": "<explication courte en francais, 2-3 phrases max>"
}}

REGLES IMPORTANTES :
- Sois conservateur : si les infos sont insuffisantes, mets une confiance faible et SKIP
- Un edge de moins de 5% n'est pas exploitable → SKIP
- Prends en compte la date de fin et l'etat actuel des evenements
- "BUY_YES" veut dire que le prix YES est trop bas (opportunite d'achat)
- "BUY_NO" veut dire que le prix YES est trop haut (= acheter NO)"""

        try:
            response = self.llm.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()

            # Parser le JSON (enlever les backticks markdown si presents)
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            analysis = json.loads(raw)

            # Valider les champs
            analysis["estimated_probability"] = max(0, min(1, float(analysis.get("estimated_probability", 0.5))))
            analysis["confidence"] = max(0, min(100, int(analysis.get("confidence", 0))))
            analysis["edge_pct"] = float(analysis.get("edge_pct", 0))
            analysis["direction"] = analysis.get("direction", "SKIP")
            analysis["risk_level"] = analysis.get("risk_level", "HIGH")
            analysis["reasoning"] = analysis.get("reasoning", "Analyse non disponible")

            return analysis

        except json.JSONDecodeError:
            return {
                "estimated_probability": 0.5,
                "confidence": 0,
                "direction": "SKIP",
                "edge_pct": 0,
                "risk_level": "HIGH",
                "reasoning": f"Erreur de parsing JSON: {raw[:200]}",
            }
        except Exception as e:
            return {
                "estimated_probability": 0.5,
                "confidence": 0,
                "direction": "SKIP",
                "edge_pct": 0,
                "risk_level": "HIGH",
                "reasoning": f"Erreur API Claude: {e}",
            }

    # =========================================================================
    # OPPORTUNITY SCORING
    # =========================================================================

    def _score_opportunity(self, market, analysis):
        """
        Score final d'une opportunite (0-100).

        Combine :
        - Edge estime (taille de l'opportunite)
        - Confiance de l'IA
        - Volume/liquidite du marche (faisabilite)
        - Risque
        """
        edge = abs(analysis.get("edge_pct", 0))
        confidence = analysis.get("confidence", 0)
        direction = analysis.get("direction", "SKIP")
        risk = analysis.get("risk_level", "HIGH")

        if direction == "SKIP":
            return 0

        # Score edge (0-35) : plus l'edge est gros, mieux c'est
        if edge >= 20:
            edge_score = 35
        elif edge >= 10:
            edge_score = 25
        elif edge >= 5:
            edge_score = 15
        else:
            edge_score = 5

        # Score confiance (0-35)
        confidence_score = (confidence / 100) * 35

        # Score volume (0-15) : marche liquide = plus facile a executer
        volume = market.get("volume", 0)
        if volume >= 100000:
            volume_score = 15
        elif volume >= 50000:
            volume_score = 12
        elif volume >= 10000:
            volume_score = 8
        else:
            volume_score = 4

        # Score risque (0-15)
        risk_scores = {"LOW": 15, "MEDIUM": 10, "HIGH": 3}
        risk_score = risk_scores.get(risk, 3)

        total = edge_score + confidence_score + volume_score + risk_score
        return min(100, round(total))

    # =========================================================================
    # MAIN SCAN
    # =========================================================================

    def scan(self, limit=100, min_volume=None, max_analyze=None):
        """
        Scan complet : decouverte → recherche → analyse → classement.

        Args:
            limit: nombre de marches a fetcher
            min_volume: volume minimum ($)
            max_analyze: nombre max de marches a analyser avec l'IA

        Returns:
            Liste d'opportunites triees par score.
        """
        max_to_analyze = max_analyze or self.MAX_MARKETS_PER_SCAN

        print(f"\n{'='*70}")
        print(f"  POLY-INSTANT AI AGENT - Scan {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"{'='*70}\n")

        # 1. Decouverte
        candidates = self.discover_markets(limit=limit, min_volume=min_volume)
        if not candidates:
            print("  Aucun marche candidat trouve.")
            return []

        to_analyze = candidates[:max_to_analyze]
        print(f"\n  Analyse de {len(to_analyze)} marches avec l'IA...\n")

        # 2. Recherche + Analyse
        opportunities = []
        for i, market in enumerate(to_analyze, 1):
            question = market["question"]
            short_q = question[:55] + "..." if len(question) > 55 else question
            print(f"  [{i}/{len(to_analyze)}] {short_q}")
            print(f"           Prix: YES={market['yes_price']:.0%} | Vol: ${market['volume']:,.0f}")

            # Recherche web
            print(f"           Recherche web...", end="", flush=True)
            research = self._research_market(market)
            n_results = research.count("[") if research != "Aucun resultat de recherche web trouve." else 0
            print(f" {n_results} sources", flush=True)

            # Analyse IA
            print(f"           Analyse IA...", end="", flush=True)
            analysis = self._analyze_with_ai(market, research)
            print(f" {analysis['direction']} (edge: {analysis['edge_pct']:+.1f}%, confiance: {analysis['confidence']}%)", flush=True)

            # Score
            score = self._score_opportunity(market, analysis)

            opportunities.append({
                "market": market,
                "analysis": analysis,
                "score": score,
                "research_sources": n_results,
            })

            # Petit delai pour pas flood les APIs
            time.sleep(0.5)

        # 3. Classement (filtrer les SKIP, trier par score)
        opportunities = [o for o in opportunities if o["analysis"]["direction"] != "SKIP"]
        opportunities.sort(key=lambda x: x["score"], reverse=True)

        return opportunities

    def format_results(self, opportunities):
        """Formate les resultats pour affichage console."""
        if not opportunities:
            print("\n  Aucune opportunite detectee ce scan.")
            print("  (tous les marches analyses sont a leur juste prix ou confiance trop faible)")
            return

        print(f"\n{'='*70}")
        print(f"  TOP OPPORTUNITES ({len(opportunities)} trouvees)")
        print(f"{'='*70}\n")

        for i, opp in enumerate(opportunities, 1):
            m = opp["market"]
            a = opp["analysis"]
            score = opp["score"]

            direction = a["direction"]
            direction_display = "ACHETER YES" if direction == "BUY_YES" else "ACHETER NO"
            risk_emoji_map = {"LOW": "Faible", "MEDIUM": "Moyen", "HIGH": "Eleve"}

            print(f"  {'─'*66}")
            print(f"  #{i} | Score: {score}/100 | {direction_display} | Risque: {risk_emoji_map.get(a['risk_level'], '?')}")
            print(f"  {'─'*66}")
            print(f"  Marche:  {m['question']}")
            print(f"  Prix:    YES={m['yes_price']:.1%} → IA estime {a['estimated_probability']:.1%} (edge: {a['edge_pct']:+.1f}%)")
            print(f"  Volume:  ${m['volume']:,.0f} | Confiance IA: {a['confidence']}%")
            print(f"  Raison:  {a['reasoning']}")
            if m.get("slug"):
                print(f"  URL:     https://polymarket.com/event/{m['slug']}")
            print()

    def notify_opportunities(self, opportunities, min_score=40):
        """Envoie les meilleures opportunites par Telegram."""
        if not self.notifier or not self.notifier.enabled:
            return

        top = [o for o in opportunities if o["score"] >= min_score]
        if not top:
            return

        lines = ["<b>AI Agent - Opportunites detectees</b>\n"]
        for i, opp in enumerate(top[:5], 1):
            m = opp["market"]
            a = opp["analysis"]
            direction = "YES" if a["direction"] == "BUY_YES" else "NO"
            lines.append(
                f"{i}. <b>{m['question'][:60]}</b>\n"
                f"   → ACHETER {direction} | Edge: {a['edge_pct']:+.1f}% | Score: {opp['score']}/100\n"
                f"   Prix: {m['yes_price']:.0%} → IA: {a['estimated_probability']:.0%} | Confiance: {a['confidence']}%\n"
                f"   {a['reasoning'][:100]}\n"
            )

        self.notifier.send("\n".join(lines))

    # =========================================================================
    # CONTINUOUS MODE
    # =========================================================================

    def run(self, interval=300, limit=100, min_volume=None, max_analyze=None, min_notify_score=40):
        """
        Mode continu : scan toutes les X secondes.

        Args:
            interval: secondes entre chaque scan (defaut: 5 min)
            limit: nombre de marches a fetcher
            min_volume: volume minimum ($)
            max_analyze: nombre max de marches a analyser
            min_notify_score: score minimum pour notifier
        """
        print(f"\n[poly-instant] AI Agent - Mode continu (interval: {interval}s)")
        print(f"  Ctrl+C pour arreter\n")

        scan_num = 0
        while True:
            try:
                scan_num += 1
                print(f"\n{'#'*70}")
                print(f"  SCAN #{scan_num}")
                print(f"{'#'*70}")

                opportunities = self.scan(
                    limit=limit,
                    min_volume=min_volume,
                    max_analyze=max_analyze,
                )
                self.format_results(opportunities)
                self.notify_opportunities(opportunities, min_score=min_notify_score)

                print(f"\n  Prochain scan dans {interval}s...")
                time.sleep(interval)

            except KeyboardInterrupt:
                print(f"\n\n  Agent arrete. {scan_num} scans effectues.")
                break
            except Exception as e:
                print(f"\n  Erreur pendant le scan: {e}")
                print(f"  Retry dans {interval}s...")
                time.sleep(interval)
