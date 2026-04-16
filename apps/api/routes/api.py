from __future__ import annotations

from collections import Counter as _Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import RedirectResponse, Response

from apps.api.dependencies.auth import require_api_key
from apps.api.dependencies.rate_limit import require_rate_limit
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
    for doc in docs:
        if doc.status != "ok":
            EXTRACTION_FAILURES.inc()
            ERRORS.labels(endpoint="extract", error_type="extraction_failure").inc()
    successes = [d for d in docs if d.status == "ok"]
    failures_list = [d for d in docs if d.status != "ok"]
    body = ExtractResponse(
        documents=[d.model_dump(mode="json") for d in docs],
        success_count=len(successes),
        failure_count=len(failures_list),
    )
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
    warnings = None

    if payload.include_answer and results:
        docs = await extract_service.extract_many([str(r.url) for r in results[:3]], format="text")
        passages = []
        for doc in docs:
            passages.extend(doc.passages[:4])
        ranked = rerank_passages(payload.query, passages)
        quick_answer = "\n\n".join([p.text for p in ranked[:2]]) if ranked else "No grounded answer available."
        failed_urls = [d.url for d in docs if d.status != "ok"]
        warnings = [f"extraction_failed: {u}" for u in failed_urls] or None
        if payload.include_raw_content:
            extracted = [d.model_dump(mode="json") for d in docs]

    body = SearchResponse(
        query=payload.query,
        results=[r.model_dump(mode="json") for r in results],
        quick_answer=quick_answer,
        citations=citations,
        extracted=extracted,
        warnings=warnings,
    )
    cache.set(key, body.model_dump(mode="json"))
    return body


@protected_router.post("/crawl", response_model=CrawlResponse)
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
            concurrency=payload.concurrency,
            allowed_domains=payload.allowed_domains,
            timeout_seconds=payload.timeout_seconds,
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


@protected_router.post("/research", response_model=ResearchResponse)
async def research(payload: ResearchRequest) -> ResearchResponse:
    REQ_COUNTER.labels(endpoint="research").inc()
    key = (
        f"research:{payload.query}:{payload.mode}:{payload.max_sources}:{payload.max_search_rounds}"
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
            )
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
