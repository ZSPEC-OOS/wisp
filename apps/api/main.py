from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from apps.api.config import settings
from apps.api.routes.api import router
from packages.common.logging import _request_id_var, configure_logging

configure_logging(settings.log_level)

app = FastAPI(title="WISP API", version="0.1.0", description="Free, self-hostable web research platform")


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


app.add_middleware(RequestIDMiddleware)
app.include_router(router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "WISP API",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})
