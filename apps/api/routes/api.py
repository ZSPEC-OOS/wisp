from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid as _uuid
from collections import Counter as _Counter
from datetime import datetime as _datetime, timezone as _tz

from fastapi import APIRouter, Depends, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import RedirectResponse, Response, StreamingResponse

from apps.api.config import settings
from apps.api.dependencies.auth import require_api_key
from apps.api.dependencies.rate_limit import make_rate_limit_dep, require_rate_limit
from apps.api.dependencies.services import cache, crawl_service, extract_service, map_service, research_service, search_service
from apps.api.schemas.requests import AcademicRequest, CrawlRequest, ExtractRequest, MapRequest, ResearchRequest, SearchRequest
from apps.api.schemas.responses import AcademicPaperResult, AcademicResponse, CrawlJobResponse, CrawlResponse, ExtractResponse, HealthResponse, MapResponse, ResearchResponse, SearchResponse
from packages.search.pipeline import rerank_passages

_db_logger = logging.getLogger("wisp.db_write")

# In-memory crawl job store (fallback when Redis is not configured)
_crawl_jobs: dict[str, dict] = {}

# ── Crawl job persistence helpers ────────────────────────────────────────────

_JOB_TTL = 3600  # seconds — jobs auto-expire from Redis after 1 h

async def _job_set(job_id: str, data: dict) -> None:
    """Write job data. Redis when available, else in-memory dict."""
    if settings.redis_url:
        try:
            from apps.api.dependencies.services import cache as _cache
            if hasattr(_cache, "_get_redis"):
                r = await _cache._get_redis()
                await r.setex(
                    f"wisp:job:{job_id}",
                    _JOB_TTL,
                    _json.dumps(data, default=str),
                )
                return
        except Exception:
            pass
    _crawl_jobs[job_id] = data


async def _job_update(job_id: str, patch: dict) -> None:
    """Merge patch into existing job. Redis when available, else in-memory."""
    if settings.redis_url:
        try:
            from apps.api.dependencies.services import cache as _cache
            if hasattr(_cache, "_get_redis"):
                r = await _cache._get_redis()
                raw = await r.get(f"wisp:job:{job_id}")
                current = _json.loads(raw) if raw else {}
                current.update(patch)
                await r.setex(f"wisp:job:{job_id}", _JOB_TTL, _json.dumps(current, default=str))
                return
        except Exception:
            pass
    if job_id in _crawl_jobs:
        _crawl_jobs[job_id].update(patch)


async def _job_get(job_id: str) -> dict | None:
    """Fetch job data. Redis when available, else in-memory."""
    if settings.redis_url:
        try:
            from apps.api.dependencies.services import cache as _cache
            if hasattr(_cache, "_get_redis"):
                r = await _cache._get_redis()
                raw = await r.get(f"wisp:job:{job_id}")
                return _json.loads(raw) if raw else None
        except Exception:
            pass
    return _crawl_jobs.get(job_id)

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
_academic_rate_limit = make_rate_limit_dep(lambda: settings.academic_rate_limit_per_minute)


# ── Fire-and-forget DB write helpers ─────────────────────────────────────────

async def _persist_search(query: str) -> None:
    try:
        from packages.storage.database import get_session
        from packages.storage.models import Query
        async with get_session() as s:
            s.add(Query(query=query[:500]))
    except Exception as exc:
        _db_logger.debug("search_persist_failed", extra={"error": str(exc)})


async def _persist_research(query: str, mode: str, out: dict) -> None:
    try:
        from packages.storage.database import get_session
        from packages.storage.models import ResearchTask
        trace = out.get("research_trace", {})
        payload = {
            "confidence_score": out.get("confidence_score", 0.0),
            "timing_ms":        trace.get("timing_ms", {}),
            "sources_count":    trace.get("sources_considered", 0),
            "llm_invoked":      trace.get("llm", {}).get("llm_invoked", False),
        }
        async with get_session() as s:
            s.add(ResearchTask(query=query[:1000], mode=mode, result=payload))
    except Exception as exc:
        _db_logger.debug("research_persist_failed", extra={"error": str(exc)})


@public_router.get("/livez")
async def livez() -> Response:
    """Liveness probe — returns 200 as long as the process is running."""
    return Response(content='{"status":"alive"}', media_type="application/json")


@public_router.get("/readyz")
async def readyz() -> Response:
    """Readiness probe — checks that required dependencies are reachable."""
    import httpx as _httpx
    checks: dict[str, str] = {}

    if settings.searxng_url:
        try:
            async with _httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{settings.searxng_url}/healthz")
            checks["searxng"] = "ok" if r.status_code < 400 else "degraded"
        except Exception:
            checks["searxng"] = "unreachable"

    if settings.llm_enabled:
        try:
            async with _httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{settings.llm_base_url}/models")
            checks["llm"] = "ok" if r.status_code < 400 else "degraded"
        except Exception:
            checks["llm"] = "unreachable"

    degraded = any(v != "ok" for v in checks.values())
    status_code = 503 if degraded else 200
    return Response(
        content=f'{{"status":"{"degraded" if degraded else "ready"}","checks":{__import__("json").dumps(checks)}}}',
        media_type="application/json",
        status_code=status_code,
    )


@public_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Detailed health — checks cache, LLM, and search provider."""
    import httpx as _httpx
    stats = await cache.stats()
    checks: dict[str, dict] = {"cache": {"status": "ok", "size": stats["size"]}}

    if settings.searxng_url:
        try:
            async with _httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{settings.searxng_url}/healthz")
            checks["searxng"] = {"status": "ok" if r.status_code < 400 else "degraded"}
        except Exception as exc:
            checks["searxng"] = {"status": "unreachable", "error": str(exc)[:120]}

    if settings.llm_enabled:
        try:
            async with _httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{settings.llm_base_url}/models")
            checks["llm"] = {"status": "ok" if r.status_code < 400 else "degraded"}
        except Exception as exc:
            checks["llm"] = {"status": "unreachable", "error": str(exc)[:120]}

    overall = "ok" if all(v.get("status") == "ok" for v in checks.values()) else "degraded"
    return HealthResponse(
        status=overall,
        version="1.0.0",
        cache_size=stats["size"],
        cache_max_size=stats["max_size"],
        checks=checks,
    )


@public_router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@protected_router.post("/extract", response_model=ExtractResponse)
async def extract(payload: ExtractRequest) -> ExtractResponse:
    REQ_COUNTER.labels(endpoint="extract").inc()
    key = f"extract:{','.join(sorted(str(u) for u in payload.urls))}:{payload.format}:{payload.include_images}"
    cached = await cache.get(key)
    if cached:
        CACHE_HITS.labels(endpoint="extract").inc()
        return ExtractResponse(**cached)
    CACHE_MISSES.labels(endpoint="extract").inc()

    with LATENCY.labels(stage="extract").time():
        docs = await extract_service.extract_many([str(u) for u in payload.urls], payload.format, payload.include_images, payload.js_render)

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
        await cache.set(key, body.model_dump(mode="json"), ttl=3600)
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
    cached = await cache.get(key)
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
        await cache.set(key, body.model_dump(mode="json"))
    asyncio.create_task(_persist_search(payload.query))
    return body


@protected_router.post("/crawl", response_model=CrawlResponse, dependencies=[Depends(_crawl_rate_limit)])
async def crawl(payload: CrawlRequest) -> CrawlResponse:
    REQ_COUNTER.labels(endpoint="crawl").inc()
    key = (
        f"crawl:{payload.seed_url}:{payload.max_pages}:{payload.max_depth}:{payload.concurrency}"
        f":{','.join(sorted(payload.allowed_domains or []))}"
    )
    cached = await cache.get(key)
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
    await cache.set(key, out, ttl=300)
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
    cached = await cache.get(key)
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
        await cache.set(key, body.model_dump(mode="json"))
        asyncio.create_task(_persist_research(payload.query, payload.mode, out))
        return body
    except Exception as exc:
        ERRORS.labels(endpoint="research", error_type=type(exc).__name__).inc()
        raise HTTPException(status_code=500, detail=f"research_failed: {exc}") from exc


@protected_router.post("/research/stream", dependencies=[Depends(_research_rate_limit)])
async def research_stream(payload: ResearchRequest) -> StreamingResponse:
    """SSE endpoint that streams research progress events then the final result.

    Events: searching | extracting | synthesizing | done | error
    Each line is:  event: <name>\\ndata: <json>\\n\\n
    """
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def _on_progress(event: str, data: dict) -> None:
        await queue.put((event, data))

    async def _run() -> None:
        try:
            result = await research_service.run(
                payload.query,
                payload.mode,
                payload.max_sources,
                max_search_rounds=payload.max_search_rounds,
                allowed_domains=payload.allowed_domains,
                blocked_domains=payload.blocked_domains,
                synthesis_mode=payload.synthesis_mode,
                on_progress=_on_progress,
            )
            await queue.put(("done", result))
        except Exception as exc:
            await queue.put(("error", {"detail": str(exc)}))
        finally:
            await queue.put(None)

    async def _event_gen():
        task = asyncio.create_task(_run())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    yield 'event: error\ndata: {"detail":"stream_timeout"}\n\n'
                    break
                if item is None:
                    break
                event, data = item
                yield f"event: {event}\ndata: {_json.dumps(data, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@protected_router.post(
    "/academic",
    response_model=AcademicResponse,
    dependencies=[Depends(_academic_rate_limit)],
)
async def academic(payload: AcademicRequest) -> AcademicResponse:
    """Prompt → search → in-memory PDF fetch (OA + optional Sci-Hub) → text → answer."""
    REQ_COUNTER.labels(endpoint="academic").inc()

    from packages.academic_pipeline.pipeline import AcademicPipeline, PipelineConfig

    # Sci-Hub fallback requires explicit opt-in at both request and config level
    use_scihub = payload.use_scihub and settings.academic_scihub_enabled
    cfg = PipelineConfig(
        prompt=payload.prompt,
        question=payload.question,
        max_papers=min(payload.max_papers, settings.academic_pipeline_max_papers),
        use_scihub=use_scihub,
        mailto=settings.academic_mailto,
        s2_api_key=settings.s2_api_key,
        llm_base_url=settings.llm_base_url if settings.llm_enabled else "",
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model if settings.llm_enabled else "",
        llm_timeout=settings.llm_timeout_seconds,
    )

    try:
        with LATENCY.labels(stage="academic").time():
            results = await AcademicPipeline(cfg).run()
    except Exception as exc:
        ERRORS.labels(endpoint="academic", error_type=type(exc).__name__).inc()
        raise HTTPException(status_code=500, detail=f"academic_pipeline_failed: {exc}") from exc

    papers = [
        AcademicPaperResult(
            title=r.title,
            doi=r.doi,
            authors=r.authors,
            publication_year=r.publication_year,
            url=r.url,
            oa_pdf_url=r.oa_pdf_url,
            content_fetched=r.content_fetched,
            parse_error=r.parse_error,
            answer=r.answer,
            provider=r.provider,
        )
        for r in results
    ]
    return AcademicResponse(
        prompt=payload.prompt,
        question=payload.question,
        papers=papers,
        papers_found=len(papers),
        content_fetched=sum(1 for p in papers if p.content_fetched),
        answers_generated=sum(1 for p in papers if p.answer),
    )


@protected_router.post("/crawl/jobs", response_model=CrawlJobResponse, dependencies=[Depends(_crawl_rate_limit)])
async def start_crawl_job(payload: CrawlRequest) -> CrawlJobResponse:
    """Start an async crawl; returns a job_id to poll via GET /crawl/jobs/{job_id}.

    Jobs persist in Redis (when configured) so any worker can serve the poll request.
    Falls back to in-memory when Redis is not available.
    """
    # Prune in-memory stale jobs (Redis handles expiry natively via SETEX TTL)
    cutoff = _datetime.now(_tz.utc).timestamp() - _JOB_TTL
    stale = [jid for jid, j in _crawl_jobs.items()
             if j["status"] in ("done", "failed") and j.get("ts", 0) < cutoff]
    for jid in stale:
        del _crawl_jobs[jid]

    job_id = _uuid.uuid4().hex
    created_at = _datetime.now(_tz.utc).isoformat()
    await _job_set(job_id, {
        "status": "pending", "created_at": created_at,
        "result": None, "error": None,
        "ts": _datetime.now(_tz.utc).timestamp(),
    })
    REQ_COUNTER.labels(endpoint="crawl_job").inc()

    async def _run_job() -> None:
        await _job_update(job_id, {"status": "running"})
        try:
            out = await crawl_service.crawl(
                seed_url=str(payload.seed_url),
                max_pages=payload.max_pages,
                max_depth=payload.max_depth,
                allowed_domains=payload.allowed_domains,
                timeout_seconds=payload.timeout_seconds,
                concurrency=payload.concurrency,
            )
            await _job_update(job_id, {"status": "done", "result": out})
        except Exception as exc:
            await _job_update(job_id, {"status": "failed", "error": str(exc)})

    asyncio.create_task(_run_job())
    return CrawlJobResponse(job_id=job_id, status="pending", created_at=created_at)


@protected_router.get("/crawl/jobs/{job_id}", response_model=CrawlJobResponse)
async def get_crawl_job(job_id: str) -> CrawlJobResponse:
    """Poll the status of an async crawl job."""
    job = await _job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    return CrawlJobResponse(
        job_id=job_id,
        status=job["status"],
        created_at=job["created_at"],
        result=job.get("result"),
        error=job.get("error"),
    )


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
