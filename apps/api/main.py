from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.api.config import settings
from apps.api.routes.api import router
from packages.common.logging import configure_logging

configure_logging(settings.log_level)

app = FastAPI(title="WISP API", version="0.1.0", description="Free, self-hostable web research platform")
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
