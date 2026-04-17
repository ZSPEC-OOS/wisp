from __future__ import annotations

import asyncio
import json
import logging
import time

from packages.common.models import Citation, ExtractedDocument, Passage, SearchResult
from packages.extract.service import ExtractService
from packages.research.evidence import (
    EvidenceChunk,
    build_evidence_chunks,
    build_evidence_profile,
)
from packages.research.llm import LlmSynthesisClient
from packages.research.synthesis_policy import should_use_llm
from packages.search.enrichers import CrossRefEnricher, UnpaywallResolver
from apps.api.config import settings
from packages.search.pipeline import SearchService, _embedding_rerank, rerank_passages

logger = logging.getLogger(__name__)


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


def _build_native_output(
    mode: str, ranked: list[Passage], citations: list[Citation]
) -> tuple[str, str, str, dict | None]:
    """Return (final_answer, executive_summary, detailed_report, structured_answer).

    Pure heuristic extractive path — unchanged from original implementation.
    """
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
        "findings":   [p.text for p in ranked[1 : mid + 1]],
        "gaps":       [p.text for p in ranked[mid + 1 :]] or ["No additional gaps identified."],
    }
    return json.dumps(structured), top1, top3, structured


def _build_native_structured_answer(ranked: list[Passage]) -> dict | None:
    """Build structured_answer natively; always used regardless of LLM path (spec §11.2)."""
    if not ranked:
        return None
    mid = len(ranked) // 2
    return {
        "background": ranked[0].text,
        "findings":   [p.text for p in ranked[1 : mid + 1]],
        "gaps":       [p.text for p in ranked[mid + 1 :]] or ["No additional gaps identified."],
    }


class ResearchService:
    def __init__(
        self,
        search: SearchService,
        extract: ExtractService,
        unpaywall: UnpaywallResolver | None = None,
        crossref: CrossRefEnricher | None = None,
        llm: LlmSynthesisClient | None = None,
    ):
        self.search    = search
        self.extract   = extract
        self.unpaywall = unpaywall
        self.crossref  = crossref
        self.llm       = llm

    async def run(
        self,
        query: str,
        mode: str = "concise",
        max_sources: int = 5,
        max_search_rounds: int = 2,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        synthesis_mode: str | None = None,
    ) -> dict:
        t0 = time.perf_counter()

        # synthesis_mode: request-level override → config default
        effective_synthesis_mode = synthesis_mode or settings.llm_synthesis_mode_default

        # ── Round 1: seed queries from AND-clause splitting ───────────────
        queries: list[str] = [query]
        if " and " in query.lower():
            queries.extend([q.strip() for q in query.split(" and ") if q.strip()])

        all_results: list[SearchResult] = []
        executed_queries: list[str] = []

        for q in queries:
            executed_queries.append(q)
            try:
                results = await asyncio.wait_for(
                    self.search.search(q, max_results=max_sources),
                    timeout=settings.search_timeout_seconds,
                )
                all_results.extend(results)
            except asyncio.TimeoutError:
                logger.warning("Search timed out for query: %r", q)

        t_search = time.perf_counter()

        # Prefetch top round-1 results so HTTP requests overlap subsequent rounds.
        # All quick-extracted docs are cached so the final extraction can reuse them.
        _extract_cache: dict[str, ExtractedDocument] = {}
        _prefetch_top3_urls = [
            str(r.url)
            for r in list({str(r.url): r for r in all_results}.values())[:3]
        ]
        _prefetch_task: asyncio.Task | None = (
            asyncio.create_task(self.extract.extract_many(_prefetch_top3_urls))
            if max_search_rounds > 1
            else None
        )

        # ── Additional rounds ─────────────────────────────────────────────
        for _round in range(1, max_search_rounds):
            unique_so_far = list({str(r.url): r for r in all_results}.values())[:max_sources]
            top3_urls = [str(r.url) for r in unique_so_far[:3]]

            if _prefetch_task is not None and top3_urls == _prefetch_top3_urls:
                quick_docs = await _prefetch_task
                _prefetch_task = None
            else:
                if _prefetch_task is not None and not _prefetch_task.done():
                    _prefetch_task.cancel()
                _prefetch_task = None
                quick_docs = await self.extract.extract_many(top3_urls)

            # Cache results so the final extraction pass can reuse them
            for d in quick_docs:
                if d.status == "ok":
                    _extract_cache[str(d.url)] = d

            quick_passages: list[Passage] = []
            for d in quick_docs:
                if d.status == "ok":
                    quick_passages.extend(d.passages[:2])
            ranked_so_far = rerank_passages(query, quick_passages)
            followup = _derive_followup_query(query, ranked_so_far)
            if not followup or followup in executed_queries:
                break
            executed_queries.append(followup)
            try:
                followup_results = await asyncio.wait_for(
                    self.search.search(followup, max_results=max_sources),
                    timeout=settings.search_timeout_seconds,
                )
                all_results.extend(followup_results)
            except asyncio.TimeoutError:
                logger.warning("Follow-up search timed out for query: %r", followup)
                break

        if _prefetch_task is not None and not _prefetch_task.done():
            _prefetch_task.cancel()

        # ── Domain filters ────────────────────────────────────────────────
        if allowed_domains:
            allowed_set = set(allowed_domains)
            all_results = [r for r in all_results if r.source_domain in allowed_set]
        if blocked_domains:
            blocked_set = set(blocked_domains)
            all_results = [r for r in all_results if r.source_domain not in blocked_set]

        unique = {str(r.url): r for r in all_results}
        top    = list(unique.values())[:max_sources]

        # ── Academic metadata enrichment (CrossRef → Unpaywall) ──────────
        # CrossRef fills publication year and authors; run first so freshness
        # scoring downstream has accurate metadata.
        if self.crossref:
            top = list(await asyncio.gather(*[self.crossref.enrich(r) for r in top]))
        # Unpaywall resolves open-access PDF URLs for DOI-bearing results.
        if self.unpaywall:
            top = list(await asyncio.gather(*[self.unpaywall.enrich(r) for r in top]))

        # Build a URL→SearchResult map for provenance binding.
        # Include both canonical URL and OA PDF URL so passages from either resolve.
        result_map: dict[str, SearchResult] = {}
        for r in top:
            result_map[str(r.url)] = r
            if r.oa_pdf_url:
                result_map[r.oa_pdf_url] = r

        extract_urls = [r.oa_pdf_url or str(r.url) for r in top]
        cached_docs  = [_extract_cache[u] for u in extract_urls if u in _extract_cache]
        fresh_urls   = [u for u in extract_urls if u not in _extract_cache]
        try:
            fresh_docs: list[ExtractedDocument] = (
                await asyncio.wait_for(
                    self.extract.extract_many(fresh_urls),
                    timeout=settings.extract_timeout_seconds,
                )
                if fresh_urls else []
            )
        except asyncio.TimeoutError:
            logger.warning("Main extraction timed out after %ss", settings.extract_timeout_seconds)
            fresh_docs = []
        docs = cached_docs + fresh_docs

        t_extract = time.perf_counter()

        # ── Passage collection and reranking ──────────────────────────────
        passages:  list[Passage]  = []
        citations: list[Citation] = []
        for d in docs:
            if d.status == "ok":
                passages.extend(d.passages[:5])
                citations.append(Citation(
                    url=d.url,
                    title=d.title,
                    snippet=(d.passages[0].text[:200] if d.passages else ""),
                ))
        ranked: list[Passage] = (
            _embedding_rerank(query, passages) if settings.enable_embeddings
            else rerank_passages(query, passages)
        )[:8]

        t_rerank = time.perf_counter()

        # ── EvidenceChunk objects (grounding backbone) ────────────────────
        evidence_chunks = build_evidence_chunks(ranked, docs, result_map)

        profile = build_evidence_profile(
            evidence_chunks,
            clear_winner_margin=settings.llm_gate_clear_winner_margin,
            clear_winner_ratio=settings.llm_gate_clear_winner_ratio,
        )

        # ── Confidence score (same formula as before, now from profile) ───
        confidence_score = profile.confidence_score

        # ── Gating decision ───────────────────────────────────────────────
        use_llm, gate_reason = should_use_llm(
            query=query,
            mode=mode,
            profile=profile,
            synthesis_mode=effective_synthesis_mode,
        )

        # Honour global off switch
        if not settings.llm_enabled or self.llm is None:
            use_llm     = False
            gate_reason = gate_reason if not use_llm else "llm_disabled"

        # ── LLM synthesis (gated, timeout-bounded, always falls back) ─────
        llm_result      = None
        llm_invoked     = False
        timeout_hit     = False
        parse_failed    = False
        fallback        = False
        llm_latency_ms  = None
        ev_count_sent   = 0
        timeout_budget  = self.llm._resolve_timeout(mode) if self.llm else 0.0

        if use_llm and self.llm is not None:
            llm_invoked   = True
            ev_slice      = evidence_chunks[: settings.llm_max_context_evidence]
            ev_count_sent = len(ev_slice)
            t_llm_start   = time.perf_counter()
            try:
                llm_result = await asyncio.wait_for(
                    self.llm.synthesize(query=query, evidence=ev_slice, mode=mode),
                    timeout=timeout_budget,
                )
            except asyncio.TimeoutError:
                timeout_hit = True
                fallback    = True
                logger.warning(
                    "LLM synthesis timed out after %.1fs (mode=%s, gate=%s)",
                    timeout_budget, mode, gate_reason,
                )
            except Exception as exc:
                fallback = True
                if isinstance(exc, (ValueError, KeyError)):
                    parse_failed = True
                logger.warning("LLM synthesis failed (%s): %s", type(exc).__name__, exc)
            finally:
                llm_latency_ms = round((time.perf_counter() - t_llm_start) * 1000)

        t_llm = time.perf_counter()

        # ── Assemble output ───────────────────────────────────────────────
        # structured_answer is always native in v1 (spec §11.2)
        structured_answer = _build_native_structured_answer(ranked)

        if llm_result is not None:
            final_answer      = llm_result.final_answer
            executive_summary = llm_result.executive_summary or final_answer
            detailed_report   = llm_result.detailed_report   or final_answer
            uncertainty_notes = llm_result.uncertainty_notes or _build_uncertainty(citations)
        else:
            final_answer, executive_summary, detailed_report, _sa = _build_native_output(
                mode, ranked, citations
            )
            # structured mode: native _build_native_output already populates _sa;
            # use it instead of the separately-built one so format is consistent.
            if mode == "structured" and _sa is not None:
                structured_answer = _sa
            uncertainty_notes = _build_uncertainty(citations)

        # WISP owns citations, citation spans, and research trace (spec §10 hard rule)
        answer_lines   = [p.text for p in ranked[:3]]
        citation_spans = [
            {"claim": a[:120], "source_url": str(r.source_url)}
            for a, r in zip(answer_lines, ranked, strict=False)
        ]

        elapsed_total = time.perf_counter() - t0
        timeout_remaining = round(timeout_budget - (llm_latency_ms or 0) / 1000, 3) if llm_invoked else None

        return {
            "final_answer":      final_answer,
            "executive_summary": executive_summary,
            "detailed_report":   detailed_report,
            "structured_answer": structured_answer,
            "confidence_score":  confidence_score,
            "sources":           [c.model_dump() for c in citations],
            "citation_spans":    citation_spans,
            "uncertainty_notes": uncertainty_notes,
            "research_trace": {
                "queries":              executed_queries,
                "sources_considered":   len(top),
                "documents_extracted":  len([d for d in docs if d.status == "ok"]),
                "timing_ms": {
                    "search_ms":  round((t_search - t0) * 1000),
                    "extract_ms": round((t_extract - t_search) * 1000),
                    "rerank_ms":  round((t_rerank - t_extract) * 1000),
                    "llm_ms":     round((t_llm - t_rerank) * 1000),
                    "total_ms":   round(elapsed_total * 1000),
                },
                # LLM-specific fields — consumed by the route layer for metric emission
                "llm": {
                    "llm_invoked":                 llm_invoked,
                    "synthesis_mode":              effective_synthesis_mode,
                    "gate_decision":               "yes" if use_llm else "no",
                    "gate_reason":                 gate_reason,
                    "evidence_count":              profile.evidence_count,
                    "source_count":                profile.source_count,
                    "provider_count":              profile.provider_count,
                    "has_clear_winner":            profile.has_clear_winner,
                    "confidence_score":            confidence_score,
                    "evidence_count_sent":         ev_count_sent,
                    "llm_latency_ms":              llm_latency_ms,
                    "timeout_triggered":           timeout_hit,
                    "parse_failure":               parse_failed,
                    "fallback_triggered":          fallback,
                    "timeout_budget_remaining_seconds": timeout_remaining,
                },
            },
            "mode": mode,
        }


def _build_uncertainty(citations: list[Citation]) -> str:
    if len(citations) < 2:
        return "Evidence is limited."
    return "Some sources may be stale or incomplete."
