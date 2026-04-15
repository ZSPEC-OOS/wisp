from __future__ import annotations

from fastapi import APIRouter, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from apps.api.dependencies.services import cache, crawl_service, extract_service, map_service, research_service, search_service
from apps.api.schemas.requests import CrawlRequest, ExtractRequest, MapRequest, ResearchRequest, SearchRequest
from apps.api.schemas.responses import CrawlResponse, ExtractResponse, HealthResponse, MapResponse, ResearchResponse, SearchResponse
from packages.search.pipeline import rerank_passages

router = APIRouter()

REQ_COUNTER = Counter("wisp_requests_total", "Total API requests", ["endpoint"])
LATENCY = Histogram("wisp_stage_latency_seconds", "Stage latency", ["stage"])
EXTRACTION_FAILURES = Counter("wisp_extraction_failures_total", "Extraction failures")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/extract", response_model=ExtractResponse)
async def extract(payload: ExtractRequest) -> ExtractResponse:
    REQ_COUNTER.labels(endpoint="extract").inc()
    with LATENCY.labels(stage="extract").time():
        docs = await extract_service.extract_many([str(u) for u in payload.urls], payload.format, payload.include_images)
    for doc in docs:
        if doc.status != "ok":
            EXTRACTION_FAILURES.inc()
    return ExtractResponse(documents=[d.model_dump(mode="json") for d in docs])


@router.post("/search", response_model=SearchResponse)
async def search(payload: SearchRequest) -> SearchResponse:
    REQ_COUNTER.labels(endpoint="search").inc()
    key = f"search:{payload.query}:{payload.max_results}"
    cached = cache.get(key)
    if cached:
        return SearchResponse(**cached)

    with LATENCY.labels(stage="search").time():
        results = await search_service.search(payload.query, max_results=payload.max_results)

    if payload.allowed_domains:
        results = [r for r in results if r.source_domain in set(payload.allowed_domains)]
    if payload.blocked_domains:
        results = [r for r in results if r.source_domain not in set(payload.blocked_domains)]

    citations = [
        {"url": str(r.url), "title": r.title, "snippet": r.snippet}
        for r in results[: min(5, len(results))]
    ]
    quick_answer = None
    extracted = None

    if payload.include_answer and results:
        docs = await extract_service.extract_many([str(r.url) for r in results[:3]], format="text")
        passages = []
        for doc in docs:
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
    )
    cache.set(key, body.model_dump(mode="json"))
    return body


@router.post("/crawl", response_model=CrawlResponse)
async def crawl(payload: CrawlRequest) -> CrawlResponse:
    REQ_COUNTER.labels(endpoint="crawl").inc()
    with LATENCY.labels(stage="crawl").time():
        out = await crawl_service.crawl(
            seed_url=str(payload.seed_url),
            max_pages=payload.max_pages,
            max_depth=payload.max_depth,
            allowed_domains=payload.allowed_domains,
        )
    return CrawlResponse(**out)


@router.post("/map", response_model=MapResponse)
async def site_map(payload: MapRequest) -> MapResponse:
    REQ_COUNTER.labels(endpoint="map").inc()
    with LATENCY.labels(stage="map").time():
        out = await map_service.build_map(str(payload.seed_url), payload.max_pages, payload.max_depth)
    return MapResponse(**out)


@router.post("/research", response_model=ResearchResponse)
async def research(payload: ResearchRequest) -> ResearchResponse:
    REQ_COUNTER.labels(endpoint="research").inc()
    try:
        with LATENCY.labels(stage="research").time():
            out = await research_service.run(payload.query, payload.mode, payload.max_sources)
        return ResearchResponse(**out)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"research_failed: {exc}") from exc
