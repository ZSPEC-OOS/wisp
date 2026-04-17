from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from datetime import datetime, timezone

from rank_bm25 import BM25Okapi

from packages.common.models import Passage, SearchResult
from packages.common.url import canonicalize_url
from packages.ranking.scoring import trust_weight
from packages.search.providers import DuckDuckGoProvider, SearchProvider

# Optional dense embedding reranker (sentence-transformers)
_embedder = None

# Topic-aware ranking weight presets
_TOPIC_WEIGHTS: dict[str, dict[str, float]] = {
    "academic": {"rank": 0.20, "trust": 0.35, "freshness": 0.10, "relevance": 0.20, "citation": 0.15},
    "news":     {"rank": 0.20, "trust": 0.25, "freshness": 0.40, "relevance": 0.15, "citation": 0.00},
    "finance":  {"rank": 0.20, "trust": 0.30, "freshness": 0.35, "relevance": 0.15, "citation": 0.00},
    "general":  {"rank": 0.28, "trust": 0.28, "freshness": 0.18, "relevance": 0.18, "citation": 0.08},
}


def _load_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer, util as st_util
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
            _embedder._st_util = st_util
        except ImportError:
            pass
    return _embedder


def normalize_query(query: str) -> str:
    return " ".join(query.strip().split())


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """Deduplicate by DOI (when available) then by canonicalized URL.

    When two results share a DOI, the one with an oa_pdf_url is preferred.
    """
    unique: OrderedDict[str, SearchResult] = OrderedDict()
    for r in results:
        key = r.doi if r.doi else canonicalize_url(str(r.url))
        if key not in unique:
            unique[key] = r
        elif not unique[key].oa_pdf_url and r.oa_pdf_url:
            unique[key] = r  # upgrade to the copy that has an OA PDF link
    return list(unique.values())


def _rank_score(rank: int) -> float:
    """Log-normalized rank score: 1.0 for rank 1, decaying smoothly."""
    return 1.0 / (1.0 + math.log(max(rank, 1)))


def _citation_boost(citation_count: int | None) -> float:
    """Smooth log-normalized citation boost in [0, 0.15]."""
    if citation_count is None or citation_count <= 0:
        return 0.0
    return min(0.15, 0.05 * math.log10(citation_count + 1))


def _stem(token: str) -> str:
    """Minimal suffix-stripping stemmer — improves BM25 recall without new deps."""
    for suffix in ("ing", "tion", "ations", "ness", "ment", "ed", "er", "ly", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[:-len(suffix)]
    return token


def _tokenize(text: str) -> list[str]:
    return [_stem(w) for w in text.lower().split()]


def _bm25_snippet_scores(query: str, results: list[SearchResult]) -> list[float]:
    """Score each result's title+snippet against the query using BM25."""
    if not results:
        return []
    corpus = [_tokenize(r.title + " " + r.snippet) for r in results]
    bm25 = BM25Okapi(corpus)
    raw_scores = bm25.get_scores(_tokenize(query))
    max_s = max(raw_scores) if max(raw_scores) > 0 else 1.0
    return [float(s) / max_s for s in raw_scores]


def score_result(result: SearchResult) -> SearchResult:
    trust = trust_weight(result.source_domain)
    now = datetime.now(timezone.utc)

    if result.publication_year:
        # Academic content: decay over 10 years from publication year
        years_old = now.year - result.publication_year
        freshness = max(0.2, 1.0 - (years_old / 10.0))
    elif result.published_date is not None:
        # Web content with a known publish date: 90-day decay
        days_old = (now - result.published_date).days
        freshness = max(0.1, 1.0 - (days_old / 90.0))
    else:
        # No publish date available — use a neutral score rather than
        # treating retrieved_at (= now) as a proxy, which always gave 1.0.
        freshness = 0.5

    result.trust_score = trust
    result.freshness_score = freshness
    return result


class SearchService:
    def __init__(
        self,
        provider: SearchProvider | None = None,
        academic_providers: list[SearchProvider] | None = None,
    ):
        self.provider = provider or DuckDuckGoProvider()
        self.academic_providers: list[SearchProvider] = academic_providers or []

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        query = normalize_query(query)

        if topic == "academic" and self.academic_providers:
            # Fan out to all academic providers in parallel; ignore individual failures
            batches = await asyncio.gather(
                *[p.search(query, max_results=max_results, topic=topic) for p in self.academic_providers],
                return_exceptions=True,
            )
            results: list[SearchResult] = []
            for batch in batches:
                if isinstance(batch, list):
                    results.extend(batch)
        else:
            results = await self.provider.search(query, max_results=max_results, topic=topic)

        results = [score_result(r) for r in dedupe_results(results)]

        # BM25 snippet relevance scoring
        bm25_scores = _bm25_snippet_scores(query, results)
        for r, bm25_s in zip(results, bm25_scores):
            r.relevance_score = bm25_s

        # Topic-aware composite ranking profile
        w = _TOPIC_WEIGHTS.get(topic, _TOPIC_WEIGHTS["general"])
        results.sort(
            key=lambda r: (
                w["rank"] * _rank_score(r.rank)
                + w["trust"] * r.trust_score
                + w["freshness"] * r.freshness_score
                + w["relevance"] * r.relevance_score
                + w["citation"] * _citation_boost(r.citation_count)
            ),
            reverse=True,
        )
        # Reassign ranks after sort so the exposed rank field is meaningful
        for i, r in enumerate(results, start=1):
            r.rank = i
        return results[:max_results]


def rerank_passages(query: str, passages: list[Passage]) -> list[Passage]:
    if not passages:
        return []
    # Tokenize with lightweight stemming for higher recall
    corpus = [_tokenize(p.text) for p in passages]
    bm25 = BM25Okapi(corpus)
    query_tokens = _tokenize(query)
    query_token_set = set(query_tokens)
    scores = bm25.get_scores(query_tokens)
    rescored = []
    for p, s, doc_tokens in zip(passages, scores, corpus, strict=False):
        overlap = len(query_token_set.intersection(doc_tokens)) / max(1, len(query_token_set))
        p.score = float(s) + (0.001 * overlap)
        rescored.append(p)
    return sorted(rescored, key=lambda x: x.score, reverse=True)


def _embedding_rerank(query: str, passages: list[Passage]) -> list[Passage]:
    """Rerank passages using dense embeddings if sentence-transformers is available."""
    try:
        embedder = _load_embedder()
        if embedder is None or not passages:
            return rerank_passages(query, passages)
        st_util = embedder._st_util
        texts = [p.text for p in passages]
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            q_emb = executor.submit(embedder.encode, query, True).result()
            p_embs = executor.submit(embedder.encode, texts, True).result()
        scores = st_util.cos_sim(q_emb, p_embs)[0].tolist()
        for p, s in zip(passages, scores):
            p.score = float(s)
        return sorted(passages, key=lambda x: x.score, reverse=True)
    except Exception:
        return rerank_passages(query, passages)
