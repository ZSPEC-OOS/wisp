from __future__ import annotations

import asyncio
from collections import Counter as _Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import RedirectResponse, Response

from apps.api.config import settings
from apps.api.dependencies.auth import require_api_key
from apps.api.dependencies.rate_limit import make_rate_limit_dep, require_rate_limit
from apps.api.dependencies.services import cache, crawl_service, extract_service, map_service, research_service, search_service
from apps.api.schemas.requests import CrawlRequest, ExtractRequest, MapRequest, ResearchRequest, SearchRequest
from apps.api.schemas.responses import CrawlResponse, ExtractResponse, HealthResponse, MapResponse, ResearchResponse, SearchResponse
from packages.search.pipeline import rerank_passages

router = APIRouter()
public_router = APIRouter()
protected_router = APIRouter(dependencies=[Depends(require_api_key), Depends(require_rate_limit)])

REQ_COUNTER = Counter("wisp_requests_total", "Total API requests", ["endpoint"])
LATENCY = Histogram(
    "wisp_stage_latency_seconds",
    "Stage latency",
    ["stage"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf")],
)
EXTRACTION_FAILURES = Counter("wisp_extraction_failures_total", "Extraction failures")
CACHE_HITS = Counter("wisp_cache_hits_total", "Cache hits", ["endpoint"])
CACHE_MISSES = Counter("wisp_cache_misses_total", "Cache misses", ["endpoint"])
ERRORS = Counter("wisp_errors_total", "Errors by endpoint and type", ["endpoint", "error_type"])
SEARCH_PROVIDER_RESULTS = Counter(
    "wisp_search_provider_results_total",
    "Search results returned per provider",
    ["provider"],
)
CRAWL_FAILURES = Counter(
    "wisp_crawl_failures_total",
    "URLs that failed during a crawl",
    ["reason"],
)
CACHE_SIZE = Gauge("wisp_cache_size", "Current number of items in the TTL cache")
CACHE_HIT_RATE = Gauge("wisp_cache_hit_rate", "Rolling cache hit rate [0-1]")

# LLM synthesis observability
LLM_GATE = Counter(
    "wisp_llm_gate_total",
    "Gate decisions for LLM synthesis",
    ["decision", "reason"],
)
LLM_CALLS = Counter(
    "wisp_llm_calls_total",
    "LLM synthesis call outcomes",
    ["status"],
)
LLM_LATENCY = Histogram(
    "wisp_llm_latency_seconds",
    "LLM synthesis latency",
    ["mode"],
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, float("inf")],
)
LLM_EVIDENCE_COUNT = Histogram(
    "wisp_llm_prompt_evidence_count",
    "Number of evidence chunks sent to LLM",
    buckets=[1, 2, 3, 4, 5, 6, 7, 8, 10, float("inf")],
)
LLM_USED_RATIO = Gauge("wisp_llm_used_ratio", "Cumulative ratio of /research requests using LLM")
LLM_TIMEOUT_REMAINING = Gauge(
    "wisp_llm_timeout_budget_remaining_seconds",
    "Remaining timeout budget after the most recent LLM call",
)

_llm_req_total = 0
_llm_req_used  = 0

# Per-endpoint rate limit dependencies for heavier operations
_research_rate_limit = make_rate_limit_dep(lambda: settings.research_rate_limit_per_minute)
_crawl_rate_limit = make_rate_limit_dep(lambda: settings.crawl_rate_limit_per_minute)


@public_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    stats = cache.stats()
    return HealthResponse(
        status="ok",
        version="1.0.0",
        cache_size=stats["size"],
        cache_max_size=stats["max_size"],
    )


@public_router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@protected_router.post("/extract", response_model=ExtractResponse)
async def extract(payload: ExtractRequest) -> ExtractResponse:
    REQ_COUNTER.labels(endpoint="extract").inc()
    key = f"extract:{','.join(sorted(str(u) for u in payload.urls))}:{payload.format}:{payload.include_images}"
    cached = cache.get(key)
    if cached:
        CACHE_HITS.labels(endpoint="extract").inc()
        return ExtractResponse(**cached)
    CACHE_MISSES.labels(endpoint="extract").inc()

    with LATENCY.labels(stage="extract").time():
        docs = await extract_service.extract_many([str(u) for u in payload.urls], payload.format, payload.include_images)

    success_count = 0
    for doc in docs:
        if doc.status == "ok":
            success_count += 1
        else:
            EXTRACTION_FAILURES.inc()
            ERRORS.labels(endpoint="extract", error_type="extraction_failure").inc()
    failure_count = len(docs) - success_count

    body = ExtractResponse(
        documents=[d.model_dump(mode="json") for d in docs],
        success_count=success_count,
        failure_count=failure_count,
    )
    if failure_count == 0:
        cache.set(key, body.model_dump(mode="json"), ttl=3600)
    return body


@protected_router.post("/search", response_model=SearchResponse)
async def search(payload: SearchRequest) -> SearchResponse:
    REQ_COUNTER.labels(endpoint="search").inc()
    key = (
        f"search:{payload.query}:{payload.max_results}:{payload.topic}"
        f":{payload.start_date}:{payload.end_date}"
        f":{','.join(sorted(payload.allowed_domains or []))}"
        f":{','.join(sorted(payload.blocked_domains or []))}"
    )
    cached = cache.get(key)
    if cached:
        CACHE_HITS.labels(endpoint="search").inc()
        return SearchResponse(**cached)
    CACHE_MISSES.labels(endpoint="search").inc()

    with LATENCY.labels(stage="search").time():
        results = await search_service.search(payload.query, max_results=payload.max_results, topic=payload.topic)

    # Emit per-provider metrics
    provider_counts = _Counter(r.provider for r in results)
    for provider, count in provider_counts.items():
        SEARCH_PROVIDER_RESULTS.labels(provider=provider).inc(count)

    if payload.allowed_domains:
        results = [r for r in results if r.source_domain in set(payload.allowed_domains)]
    if payload.blocked_domains:
        results = [r for r in results if r.source_domain not in set(payload.blocked_domains)]
    if payload.start_date:
        results = [r for r in results if r.retrieved_at.date() >= payload.start_date]
    if payload.end_date:
        results = [r for r in results if r.retrieved_at.date() <= payload.end_date]

    citations = [
        {"url": str(r.url), "title": r.title, "snippet": r.snippet}
        for r in results[: min(5, len(results))]
    ]
    quick_answer = None
    extracted = None
    warnings: list[str] = []

    if payload.include_answer and results:
        try:
            docs = await asyncio.wait_for(
                extract_service.extract_many([str(r.url) for r in results[:3]], format="text"),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            docs = []
            warnings.append("Quick-answer extraction timed out.")
        passages = []
        for doc in docs:
            if doc.status != "ok":
                warnings.append(f"Extraction failed for {doc.url}: {doc.status}")
                EXTRACTION_FAILURES.inc()
            else:
                passages.extend(doc.passages[:4])
        ranked = rerank_passages(payload.query, passages)
        quick_answer = "\n\n".join([p.text for p in ranked[:2]]) if ranked else "No grounded answer available."
        if payload.include_raw_content:
            extracted = [d.model_dump(mode="json") for d in docs]

    body = SearchResponse(
        query=payload.query,
        results=[r.model_dump(mode="json") for r in results],
        quick_answer=quick_answer,
        citations=citations,
        extracted=extracted,
        warnings=warnings or None,
    )
    if not warnings:
        cache.set(key, body.model_dump(mode="json"))
    return body


@protected_router.post("/crawl", response_model=CrawlResponse, dependencies=[Depends(_crawl_rate_limit)])
async def crawl(payload: CrawlRequest) -> CrawlResponse:
    REQ_COUNTER.labels(endpoint="crawl").inc()
    key = (
        f"crawl:{payload.seed_url}:{payload.max_pages}:{payload.max_depth}:{payload.concurrency}"
        f":{','.join(sorted(payload.allowed_domains or []))}"
    )
    cached = cache.get(key)
    if cached:
        CACHE_HITS.labels(endpoint="crawl").inc()
        return CrawlResponse(**cached)
    CACHE_MISSES.labels(endpoint="crawl").inc()

    with LATENCY.labels(stage="crawl").time():
        out = await crawl_service.crawl(
            seed_url=str(payload.seed_url),
            max_pages=payload.max_pages,
            max_depth=payload.max_depth,
            allowed_domains=payload.allowed_domains,
            timeout_seconds=payload.timeout_seconds,
            concurrency=payload.concurrency,
        )

    for failure in out.get("failures", []):
        reason = failure.get("error", "unknown")[:64]
        CRAWL_FAILURES.labels(reason=reason).inc()

    body = CrawlResponse(**out)
    cache.set(key, out, ttl=300)
    return body


@protected_router.post("/map", response_model=MapResponse)
async def site_map(payload: MapRequest) -> MapResponse:
    REQ_COUNTER.labels(endpoint="map").inc()
    with LATENCY.labels(stage="map").time():
        out = await map_service.build_map(str(payload.seed_url), payload.max_pages, payload.max_depth)
    return MapResponse(**out)


@protected_router.post("/research", response_model=ResearchResponse, dependencies=[Depends(_research_rate_limit)])
async def research(payload: ResearchRequest) -> ResearchResponse:
    global _llm_req_total, _llm_req_used

    REQ_COUNTER.labels(endpoint="research").inc()
    # synthesis_mode is included in the cache key — a never/auto/always result
    # is not interchangeable for the same query+mode combination.
    key = (
        f"research:{payload.query}:{payload.mode}:{payload.max_sources}:{payload.max_search_rounds}"
        f":{payload.synthesis_mode}"
        f":{','.join(sorted(payload.allowed_domains or []))}"
        f":{','.join(sorted(payload.blocked_domains or []))}"
    )
    cached = cache.get(key)
    if cached:
        CACHE_HITS.labels(endpoint="research").inc()
        return ResearchResponse(**cached)
    CACHE_MISSES.labels(endpoint="research").inc()

    try:
        with LATENCY.labels(stage="research").time():
            out = await research_service.run(
                payload.query,
                payload.mode,
                payload.max_sources,
                max_search_rounds=payload.max_search_rounds,
                allowed_domains=payload.allowed_domains,
                blocked_domains=payload.blocked_domains,
                synthesis_mode=payload.synthesis_mode,
            )

        # Emit LLM observability metrics from trace metadata
        trace = out.get("research_trace", {})
        llm_meta = trace.get("llm", {})

        gate_decision = "yes" if llm_meta.get("llm_invoked") else "no"
        gate_reason   = llm_meta.get("gate_reason", "unknown")
        LLM_GATE.labels(decision=gate_decision, reason=gate_reason).inc()

        if llm_meta.get("llm_invoked"):
            status = (
                "timeout"     if llm_meta.get("timeout_triggered") else
                "parse_error" if llm_meta.get("parse_failure")     else
                "fallback"    if llm_meta.get("fallback_triggered") else
                "success"
            )
            LLM_CALLS.labels(status=status).inc()
            llm_ms = llm_meta.get("llm_latency_ms", 0)
            LLM_LATENCY.labels(mode=payload.mode).observe(llm_ms / 1000)
            ev_count = llm_meta.get("evidence_count_sent", 0)
            if ev_count:
                LLM_EVIDENCE_COUNT.observe(ev_count)
            remaining = llm_meta.get("timeout_budget_remaining_seconds")
            if remaining is not None:
                LLM_TIMEOUT_REMAINING.set(remaining)

        _llm_req_total += 1
        if llm_meta.get("llm_invoked") and not llm_meta.get("fallback_triggered"):
            _llm_req_used += 1
        LLM_USED_RATIO.set(_llm_req_used / _llm_req_total)

        body = ResearchResponse(**out)
        cache.set(key, body.model_dump(mode="json"))
        return body
    except Exception as exc:
        ERRORS.labels(endpoint="research", error_type=type(exc).__name__).inc()
        raise HTTPException(status_code=500, detail=f"research_failed: {exc}") from exc


router.include_router(public_router)
router.include_router(protected_router)

# 308 Permanent Redirect aliases so old unversioned paths remain usable
# for one release cycle while callers migrate to /v1/.
legacy_router = APIRouter(include_in_schema=False)


@legacy_router.post("/search")
async def legacy_search(_: Request) -> RedirectResponse:
    return RedirectResponse(url="/v1/search", status_code=308)


@legacy_router.post("/extract")
async def legacy_extract(_: Request) -> RedirectResponse:
    return RedirectResponse(url="/v1/extract", status_code=308)


@legacy_router.post("/crawl")
async def legacy_crawl(_: Request) -> RedirectResponse:
    return RedirectResponse(url="/v1/crawl", status_code=308)


@legacy_router.post("/map")
async def legacy_map(_: Request) -> RedirectResponse:
    return RedirectResponse(url="/v1/map", status_code=308)


@legacy_router.post("/research")
async def legacy_research(_: Request) -> RedirectResponse:
    return RedirectResponse(url="/v1/research", status_code=308)
