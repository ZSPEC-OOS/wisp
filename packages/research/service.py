from __future__ import annotations

from packages.common.models import Citation
from packages.extract.service import ExtractService
from packages.search.pipeline import SearchService, rerank_passages


class ResearchService:
    def __init__(self, search: SearchService, extract: ExtractService):
        self.search = search
        self.extract = extract

    async def run(self, query: str, mode: str = "concise", max_sources: int = 5) -> dict:
        queries = [query]
        if " and " in query.lower():
            queries.extend([q.strip() for q in query.split(" and ") if q.strip()])

        all_results = []
        for q in queries[:3]:
            all_results.extend(await self.search.search(q, max_results=max_sources))

        unique = {str(r.url): r for r in all_results}
        top = list(unique.values())[:max_sources]
        docs = await self.extract.extract_many([str(r.url) for r in top])

        passages = []
        citations = []
        for d in docs:
            if d.status == "ok":
                passages.extend(d.passages[:5])
                citations.append(Citation(url=d.url, title=d.title, snippet=(d.passages[0].text[:200] if d.passages else "")))
        ranked = rerank_passages(query, passages)[:8]

        answer_lines = [p.text for p in ranked[:3]]
        uncertainty = "Evidence is limited." if len(citations) < 2 else "Some sources may be stale or incomplete."
        return {
            "final_answer": "\n\n".join(answer_lines) if answer_lines else "Insufficient evidence from retrievable sources.",
            "executive_summary": answer_lines[0] if answer_lines else "No strong source-backed summary.",
            "detailed_report": "\n\n".join(answer_lines),
            "sources": [c.model_dump() for c in citations],
            "citation_spans": [{"claim": a[:120], "source_url": str(r.source_url)} for a, r in zip(answer_lines, ranked, strict=False)],
            "uncertainty_notes": uncertainty,
            "research_trace": {
                "queries": queries,
                "sources_considered": len(top),
                "documents_extracted": len([d for d in docs if d.status == "ok"]),
            },
            "mode": mode,
        }
