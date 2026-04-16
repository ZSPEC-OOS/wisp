from __future__ import annotations

import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from apps.api.config import settings
from apps.api.routes.api import legacy_router, protected_router, public_router
from packages.common.logging import _request_id_var, configure_logging

configure_logging(settings.log_level)

app = FastAPI(title="WISP API", version="1.0.0", description="Free, self-hostable web research platform")

_access_logger = logging.getLogger("wisp.access")
_startup_logger = logging.getLogger("wisp.startup")
_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


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


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": "request_too_large", "detail": f"Body exceeds {_MAX_BODY_BYTES} bytes"},
            )
        return await call_next(request)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(MaxBodySizeMiddleware)

# Public infrastructure endpoints at root (no version prefix)
app.include_router(public_router)
# Versioned API — canonical paths are /v1/*
app.include_router(protected_router, prefix="/v1")
# 308 redirects from legacy unversioned paths to /v1/* (backward compat)
app.include_router(legacy_router)


@app.on_event("startup")
async def _startup_checks() -> None:
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

    if settings.enable_embeddings:
        from packages.search.pipeline import _load_embedder
        _load_embedder()

    _startup_logger.info("wisp_started", extra={"version": "1.0.0", "env": settings.env})


@app.on_event("shutdown")
async def _graceful_shutdown() -> None:
    from apps.api.dependencies.services import cache

    pruned = cache._prune_expired()
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
async def unhandled_exception(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})
