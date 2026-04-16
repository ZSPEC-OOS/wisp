from __future__ import annotations

import asyncio

from packages.common.models import Citation, Passage, SearchResult
from packages.extract.service import ExtractService
from packages.search.enrichers import UnpaywallResolver
from apps.api.config import settings
from packages.search.pipeline import SearchService, _embedding_rerank, rerank_passages


def _derive_followup_query(original_query: str, passages: list[Passage]) -> str | None:
    """Extract terms from the top passage that aren't in the original query to form a follow-up."""
    if not passages:
        return None
    query_words = {w.lower() for w in original_query.split()}
    new_terms = [
        w.strip(".,!?;:\"'()[]")
        for w in passages[0].text.split()
        if len(w) > 4 and w.lower().strip(".,!?;:\"'()[]") not in query_words
    ]
    if not new_terms:
        return None
    return f"{original_query} {' '.join(new_terms[:3])}"


def _build_output(mode: str, ranked: list[Passage], citations: list[Citation]) -> tuple[str, str, str, dict | None]:
    """Return (final_answer, executive_summary, detailed_report, structured_answer) for the given mode."""
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
    structured = {
        "background": ranked[0].text if ranked else "",
        "findings": [p.text for p in ranked[1:mid + 1]],
        "gaps": [p.text for p in ranked[mid + 1:]] or ["No additional gaps identified."],
    }
    return top1, top1, top3, structured


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
        # Round 1: seed queries from AND-clause splitting
        queries: list[str] = [query]
        if " and " in query.lower():
            queries.extend([q.strip() for q in query.split(" and ") if q.strip()])

        all_results = []
        executed_queries: list[str] = []

        for q in queries:
            executed_queries.append(q)
            all_results.extend(await self.search.search(q, max_results=max_sources))

        # Additional rounds: derive follow-up queries from top-ranked passages so far
        for _round in range(1, max_search_rounds):
            unique_so_far = list({str(r.url): r for r in all_results}.values())[:max_sources]
            quick_docs = await self.extract.extract_many([str(r.url) for r in unique_so_far[:3]])
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
            top = list(
                await asyncio.gather(*[self.unpaywall.enrich(r) for r in top])
            )

        # Prefer OA PDF URL for extraction when available
        extract_urls = [r.oa_pdf_url or str(r.url) for r in top]
        docs = await self.extract.extract_many(extract_urls)

        passages: list[Passage] = []
        citations: list[Citation] = []
        for d in docs:
            if d.status == "ok":
                passages.extend(d.passages[:5])
                citations.append(Citation(url=d.url, title=d.title, snippet=(d.passages[0].text[:200] if d.passages else "")))
        ranked = (
            _embedding_rerank(query, passages) if settings.enable_embeddings
            else rerank_passages(query, passages)
        )[:8]

        final_answer, executive_summary, detailed_report, structured_answer = _build_output(mode, ranked, citations)
        answer_lines = [p.text for p in ranked[:3]]
        # Compute confidence: source diversity × average trust
        if citations:
            provider_diversity = len({str(r.provider) for r in top}) / max(1, len(top))
            avg_trust = sum(r.trust_score for r in top) / len(top)
            confidence_score = round(min(1.0, provider_diversity * avg_trust * (len(citations) / max_sources)), 3)
        else:
            confidence_score = 0.0

        if confidence_score < 0.3:
            uncertainty = "Low confidence: insufficient or low-quality sources."
        elif confidence_score < 0.6:
            uncertainty = "Moderate confidence: some sources may be stale or incomplete."
        else:
            uncertainty = "Good confidence: multiple diverse sources retrieved."
        return {
            "final_answer": final_answer,
            "executive_summary": executive_summary,
            "detailed_report": detailed_report,
            "structured_answer": structured_answer,
            "sources": [c.model_dump() for c in citations],
            "citation_spans": [{"claim": a[:120], "source_url": str(r.source_url)} for a, r in zip(answer_lines, ranked, strict=False)],
            "uncertainty_notes": uncertainty,
            "confidence_score": confidence_score,
            "research_trace": {
                "queries": executed_queries,
                "sources_considered": len(top),
                "documents_extracted": len([d for d in docs if d.status == "ok"]),
            },
            "mode": mode,
        }
