from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone

from rank_bm25 import BM25Okapi

from packages.common.models import Passage, SearchResult
from packages.common.url import canonicalize_url
from packages.search.providers import DuckDuckGoProvider, SearchProvider

_ACADEMIC_TRUSTED = {"arxiv.org", "openalex.org", "semanticscholar.org", "doi.org"}


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


def score_result(result: SearchResult) -> SearchResult:
    trusted_substrings = [".gov", ".edu", "wikipedia.org", "arxiv.org", "reuters.com",
                          "openalex.org", "semanticscholar.org"]
    trust = 0.3
    if any(t in result.source_domain for t in trusted_substrings):
        trust = 0.9
    elif result.source_domain.count(".") >= 1:
        trust = 0.6
    # Boost trust for highly-cited academic results (capped at 0.95)
    if result.citation_count and result.citation_count > 50:
        trust = min(0.95, trust + 0.05)
    days_old = (datetime.now(timezone.utc) - result.retrieved_at).days
    # Academic papers don't decay as fast — use 365-day window for dated results
    if result.publication_year:
        freshness = max(0.3, 1.0 - (days_old / 365))
    else:
        freshness = max(0.1, 1.0 - (days_old / 30))
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
        results.sort(
            key=lambda r: 0.5 * (1 / r.rank) + 0.3 * r.trust_score + 0.2 * r.freshness_score,
            reverse=True,
        )
        # Reassign ranks after sort so the exposed rank field is meaningful
        for i, r in enumerate(results, start=1):
            r.rank = i
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
