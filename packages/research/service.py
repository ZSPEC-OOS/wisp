from __future__ import annotations

import asyncio
import json
import time

from packages.common.models import Citation, Passage, SearchResult
from packages.extract.service import ExtractService
from packages.search.enrichers import UnpaywallResolver
from apps.api.config import settings
from packages.search.pipeline import SearchService, _embedding_rerank, rerank_passages


def _derive_followup_query(original_query: str, passages: list[Passage]) -> str | None:
    """Pick high-frequency terms from the top-5 passages to extend the query."""
    if not passages:
        return None
    query_words = {w.lower().strip(".,!?;:\"'()[]") for w in original_query.split()}
    freq: dict[str, int] = {}
    for p in passages[:5]:
        for w in p.text.split():
            clean = w.lower().strip(".,!?;:\"'()[]")
            if len(clean) > 4 and clean not in query_words:
                freq[clean] = freq.get(clean, 0) + 1
    if not freq:
        return None
    top_terms = sorted(freq, key=freq.__getitem__, reverse=True)[:3]
    return f"{original_query} {' '.join(top_terms)}"


def _build_output(
    mode: str, ranked: list[Passage], citations: list[Citation]
) -> tuple[str, str, str, dict | None]:
    """Return (final_answer, executive_summary, detailed_report, structured_answer)."""
    no_evidence = "Insufficient evidence from retrievable sources."
    if not ranked:
        return no_evidence, no_evidence, no_evidence, None

    top1 = ranked[0].text
    top3 = "\n\n".join(p.text for p in ranked[:3])

    if mode == "concise":
        return top1, top1, top1, None

    if mode == "report":
        sections = []
        for i, p in enumerate(ranked[:8]):
            source = citations[i].title if i < len(citations) else f"Source {i + 1}"
            sections.append(f"### {source}\n\n{p.text}")
        detailed = "\n\n".join(sections)
        return top3, top1, detailed, None

    # structured
    mid = len(ranked) // 2
    structured: dict = {
        "background": ranked[0].text if ranked else "",
        "findings": [p.text for p in ranked[1 : mid + 1]],
        "gaps": [p.text for p in ranked[mid + 1 :]] or ["No additional gaps identified."],
    }
    return json.dumps(structured), top1, top3, structured


class ResearchService:
    def __init__(
        self,
        search: SearchService,
        extract: ExtractService,
        unpaywall: UnpaywallResolver | None = None,
    ):
        self.search = search
        self.extract = extract
        self.unpaywall = unpaywall

    async def run(
        self,
        query: str,
        mode: str = "concise",
        max_sources: int = 5,
        max_search_rounds: int = 2,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> dict:
        t0 = time.perf_counter()

        # Round 1: seed queries from AND-clause splitting
        queries: list[str] = [query]
        if " and " in query.lower():
            queries.extend([q.strip() for q in query.split(" and ") if q.strip()])

        all_results: list[SearchResult] = []
        executed_queries: list[str] = []

        for q in queries:
            executed_queries.append(q)
            all_results.extend(await self.search.search(q, max_results=max_sources))

        t_search = time.perf_counter()

        # Prefetch extraction of top round-1 results so HTTP requests are in flight
        # while subsequent search rounds execute (genuine async overlap)
        _prefetch_top3_urls = [
            str(r.url)
            for r in list({str(r.url): r for r in all_results}.values())[:3]
        ]
        _prefetch_task: asyncio.Task | None = (
            asyncio.create_task(self.extract.extract_many(_prefetch_top3_urls))
            if max_search_rounds > 1
            else None
        )

        # Additional rounds: derive follow-up queries from top-ranked passages so far
        for _round in range(1, max_search_rounds):
            unique_so_far = list({str(r.url): r for r in all_results}.values())[:max_sources]
            top3_urls = [str(r.url) for r in unique_so_far[:3]]

            # Reuse prefetch task if URLs are unchanged, else cancel and re-extract
            if _prefetch_task is not None and top3_urls == _prefetch_top3_urls:
                quick_docs = await _prefetch_task
                _prefetch_task = None
            else:
                if _prefetch_task is not None:
                    _prefetch_task.cancel()
                    _prefetch_task = None
                quick_docs = await self.extract.extract_many(top3_urls)

            quick_passages: list[Passage] = []
            for d in quick_docs:
                if d.status == "ok":
                    quick_passages.extend(d.passages[:2])
            ranked_so_far = rerank_passages(query, quick_passages)
            followup = _derive_followup_query(query, ranked_so_far)
            if not followup or followup in executed_queries:
                break
            executed_queries.append(followup)
            all_results.extend(await self.search.search(followup, max_results=max_sources))

        # Cancel any unused prefetch task
        if _prefetch_task is not None:
            _prefetch_task.cancel()

        # Apply domain filters
        if allowed_domains:
            allowed_set = set(allowed_domains)
            all_results = [r for r in all_results if r.source_domain in allowed_set]
        if blocked_domains:
            blocked_set = set(blocked_domains)
            all_results = [r for r in all_results if r.source_domain not in blocked_set]

        unique = {str(r.url): r for r in all_results}
        top = list(unique.values())[:max_sources]

        # Resolve OA PDF URLs via Unpaywall for academic results that have a DOI
        if self.unpaywall:
            top = list(await asyncio.gather(*[self.unpaywall.enrich(r) for r in top]))

        # Prefer OA PDF URL for extraction when available
        extract_urls = [r.oa_pdf_url or str(r.url) for r in top]
        docs = await self.extract.extract_many(extract_urls)

        t_extract = time.perf_counter()

        passages: list[Passage] = []
        citations: list[Citation] = []
        for d in docs:
            if d.status == "ok":
                passages.extend(d.passages[:5])
                citations.append(
                    Citation(
                        url=d.url,
                        title=d.title,
                        snippet=(d.passages[0].text[:200] if d.passages else ""),
                    )
                )
        ranked = (
            _embedding_rerank(query, passages) if settings.enable_embeddings
            else rerank_passages(query, passages)
        )[:8]

        t_rerank = time.perf_counter()

        final_answer, executive_summary, detailed_report, structured_answer = _build_output(
            mode, ranked, citations
        )
        answer_lines = [p.text for p in ranked[:3]]

        # Confidence score: provider diversity × average trust score
        if top:
            provider_diversity = len({r.provider for r in top}) / len(top)
            avg_trust = sum(getattr(r, "trust_score", 0.5) for r in top) / len(top)
            confidence_score = round(min(1.0, provider_diversity * avg_trust * 2), 4)
        else:
            confidence_score = 0.0

        uncertainty = (
            "Evidence is limited."
            if len(citations) < 2
            else "Some sources may be stale or incomplete."
        )
        return {
            "final_answer": final_answer,
            "executive_summary": executive_summary,
            "detailed_report": detailed_report,
            "structured_answer": structured_answer,
            "confidence_score": confidence_score,
            "sources": [c.model_dump() for c in citations],
            "citation_spans": [
                {"claim": a[:120], "source_url": str(r.source_url)}
                for a, r in zip(answer_lines, ranked, strict=False)
            ],
            "uncertainty_notes": uncertainty,
            "research_trace": {
                "queries": executed_queries,
                "sources_considered": len(top),
                "documents_extracted": len([d for d in docs if d.status == "ok"]),
                "timing_ms": {
                    "search_ms": round((t_search - t0) * 1000),
                    "extract_ms": round((t_extract - t_search) * 1000),
                    "rerank_ms": round((t_rerank - t_extract) * 1000),
                    "total_ms": round((t_rerank - t0) * 1000),
                },
            },
            "mode": mode,
        }
