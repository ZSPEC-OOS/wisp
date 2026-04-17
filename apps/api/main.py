from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from apps.api.config import settings
from apps.api.routes.api import legacy_router, protected_router, public_router
from packages.common.logging import _request_id_var, configure_logging

configure_logging(settings.log_level)

app = FastAPI(title="WISP API", version="1.0.0", description="Free, self-hostable web research platform")

_access_logger = logging.getLogger("wisp.access")
_startup_logger = logging.getLogger("wisp.startup")


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject request bodies larger than max_bytes with HTTP 413.

    Enforces the limit on the actual stream so attackers cannot bypass it by
    omitting or falsifying the Content-Length header.
    """

    def __init__(self, app, max_bytes: int = 1_048_576):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            _too_large = JSONResponse(
                status_code=413,
                content={"error": "request_too_large", "detail": f"Body exceeds {self.max_bytes} bytes"},
            )
            # Fast path: Content-Length header present and already over limit
            if content_length and int(content_length) > self.max_bytes:
                return _too_large
            # Stream enforcement: count bytes as they arrive
            if not content_length:
                body = b""
                async for chunk in request.stream():
                    body += chunk
                    if len(body) > self.max_bytes:
                        return _too_large
                # Cache the consumed body so FastAPI can still read it downstream
                request._body = body
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        # Remove headers that reveal the server stack to attackers
        response.headers.pop("server", None)
        response.headers.pop("x-powered-by", None)
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        _access_logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _request_id_var.reset(token)


app.add_middleware(MaxBodySizeMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)

# Public infrastructure endpoints at root (no version prefix)
app.include_router(public_router)
# Versioned API — canonical paths are /v1/*
app.include_router(protected_router, prefix="/v1")
# 308 redirects from legacy unversioned paths to /v1/* (backward compat)
app.include_router(legacy_router)


@app.on_event("startup")
async def _startup_checks() -> None:
    import httpx as _httpx
    from urllib.parse import urlparse
    from apps.api.dependencies.auth import _parse_api_keys, validate_api_key_format

    if settings.rate_limit_per_minute > 0:
        workers = int(os.environ.get("WEB_CONCURRENCY", 1))
        if workers > 1:
            _startup_logger.warning(
                "rate_limiter_multi_worker",
                extra={
                    "workers": workers,
                    "effective_limit": workers * settings.rate_limit_per_minute,
                    "note": "Use a Redis-backed limiter for accurate per-key limits across workers",
                },
            )

    if settings.api_keys:
        for key in _parse_api_keys(settings.api_keys):
            if not validate_api_key_format(key):
                _startup_logger.warning(
                    "weak_api_key_detected",
                    extra={"key_length": len(key), "min_required": 16},
                )

    if settings.llm_enabled and not settings.llm_api_key:
        _startup_logger.error(
            "llm_key_missing",
            extra={"note": "llm_enabled=True but WISP_LLM_API_KEY is not set — LLM synthesis will fail at runtime"},
        )

    if settings.searxng_url:
        parsed = urlparse(settings.searxng_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            _startup_logger.error(
                "searxng_url_invalid",
                extra={"url": settings.searxng_url, "note": "WISP_SEARXNG_URL must be a valid http/https URL"},
            )
        else:
            try:
                async with _httpx.AsyncClient(timeout=3.0) as c:
                    await c.get(f"{settings.searxng_url}/healthz")
                _startup_logger.info("searxng_reachable", extra={"url": settings.searxng_url})
            except Exception as exc:
                _startup_logger.warning(
                    "searxng_unreachable",
                    extra={"url": settings.searxng_url, "error": str(exc),
                           "note": "Search will degrade to DuckDuckGo fallback"},
                )

    if settings.enable_embeddings:
        from packages.search.pipeline import _load_embedder
        _load_embedder()

    _startup_logger.info("wisp_started", extra={"version": "1.0.0", "env": settings.env})


@app.on_event("startup")
async def _start_cache_metrics_updater() -> None:
    """Background task: push cache size and hit-rate to Prometheus Gauges every 30 s."""
    from apps.api.dependencies.services import cache as _cache
    from apps.api.routes.api import CACHE_HIT_RATE, CACHE_SIZE

    async def _update_loop() -> None:
        while True:
            stats = _cache.stats()
            CACHE_SIZE.set(stats["size"])
            CACHE_HIT_RATE.set(stats["hit_rate"])
            await asyncio.sleep(30)

    asyncio.create_task(_update_loop())


@app.on_event("shutdown")
async def _shutdown_cleanup() -> None:
    from apps.api.dependencies.services import _llm_client, cache as _cache
    if _llm_client is not None:
        try:
            await _llm_client.aclose()
        except Exception:
            pass
    pruned = _cache._prune_expired()
    _startup_logger.info("wisp_shutdown", extra={"cache_pruned": pruned})


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "WISP API",
        "version": "1.0.0",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "api_base": "/v1",
    }


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": str(exc),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "detail": exc.errors(),
            "request_id": getattr(request.state, "request_id", None),
        },
    )
