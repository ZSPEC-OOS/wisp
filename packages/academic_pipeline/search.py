"""Stage 1: Search for academic papers and resolve DOIs + OA PDF URLs.

Queries arXiv, Semantic Scholar, and OpenAlex concurrently, deduplicates by
DOI, then enriches missing OA PDF URLs via Unpaywall.
"""
from __future__ import annotations

import asyncio
import logging

from packages.common.models import SearchResult
from packages.search.academic_providers import (
    ArxivProvider,
    OpenAlexProvider,
    SemanticScholarProvider,
)
from packages.search.enrichers import CrossRefEnricher, UnpaywallResolver

_logger = logging.getLogger("wisp.academic_pipeline.search")


class AcademicSearcher:
    def __init__(
        self,
        *,
        mailto: str = "",
        s2_api_key: str = "",
        timeout: int = 15,
        per_provider: int = 4,
    ) -> None:
        self._providers = [
            ArxivProvider(timeout=timeout, max_results=per_provider),
            SemanticScholarProvider(api_key=s2_api_key, timeout=timeout, limit=per_provider),
            OpenAlexProvider(mailto=mailto, timeout=timeout, per_page=per_provider),
        ]
        self._unpaywall = UnpaywallResolver(email=mailto, timeout=timeout)
        self._crossref = CrossRefEnricher(mailto=mailto, timeout=timeout)

    async def search(self, prompt: str, max_papers: int = 5) -> list[SearchResult]:
        """Return up to *max_papers* deduplicated results with OA PDF URLs."""
        batches = await asyncio.gather(
            *[p.search(prompt, max_results=max_papers) for p in self._providers],
            return_exceptions=True,
        )

        seen_dois: set[str] = set()
        seen_urls: set[str] = set()
        merged: list[SearchResult] = []
        for batch in batches:
            if isinstance(batch, Exception):
                _logger.warning("provider_failed: %s", batch)
                continue
            for r in batch:
                key = r.doi or str(r.url)
                if r.doi and r.doi in seen_dois:
                    continue
                if str(r.url) in seen_urls:
                    continue
                if r.doi:
                    seen_dois.add(r.doi)
                seen_urls.add(str(r.url))
                merged.append(r)
                if len(merged) >= max_papers * 3:
                    break

        # Enrich in parallel — best-effort, never discard a result on failure
        enriched = await asyncio.gather(
            *[self._enrich(r) for r in merged], return_exceptions=True
        )
        results: list[SearchResult] = []
        for orig, res in zip(merged, enriched):
            results.append(orig if isinstance(res, Exception) else res)

        # Re-sort: prefer results that already have an OA PDF URL
        results.sort(key=lambda r: (r.oa_pdf_url is None, r.rank))
        return results[:max_papers]

    async def _enrich(self, result: SearchResult) -> SearchResult:
        result = await self._crossref.enrich(result)
        result = await self._unpaywall.enrich(result)
        return result
