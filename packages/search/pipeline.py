from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone

from rank_bm25 import BM25Okapi

from packages.common.models import Passage, SearchResult
from packages.common.url import canonicalize_url
from packages.search.providers import DuckDuckGoProvider, SearchProvider


def normalize_query(query: str) -> str:
    return " ".join(query.strip().split())


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    unique = OrderedDict()
    for r in results:
        key = canonicalize_url(str(r.url))
        if key not in unique:
            unique[key] = r
    return list(unique.values())


def score_result(result: SearchResult) -> SearchResult:
    trusted = [".gov", ".edu", "wikipedia.org", "arxiv.org", "reuters.com"]
    trust = 0.3
    if any(t in result.source_domain for t in trusted):
        trust = 0.9
    elif result.source_domain.count(".") >= 1:
        trust = 0.6
    days_old = (datetime.now(timezone.utc) - result.retrieved_at).days
    freshness = max(0.1, 1.0 - (days_old / 30))
    result.trust_score = trust
    result.freshness_score = freshness
    return result


class SearchService:
    def __init__(self, provider: SearchProvider | None = None):
        self.provider = provider or DuckDuckGoProvider()

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        query = normalize_query(query)
        results = await self.provider.search(query, max_results=max_results, topic=topic)
        results = [score_result(r) for r in dedupe_results(results)]
        results.sort(
            key=lambda r: 0.5 * (1 / r.rank) + 0.3 * r.trust_score + 0.2 * r.freshness_score,
            reverse=True,
        )
        return results[:max_results]


def rerank_passages(query: str, passages: list[Passage]) -> list[Passage]:
    if not passages:
        return []
    corpus = [p.text.split() for p in passages]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query.split())
    rescored = []
    for p, s in zip(passages, scores, strict=False):
        p.score = float(s)
        rescored.append(p)
    return sorted(rescored, key=lambda x: x.score, reverse=True)
