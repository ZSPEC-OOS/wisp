from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import settings
from packages.storage.models import Base

_logger = logging.getLogger("wisp.db")


def _make_engine():
    url = settings.db_url
    if "sqlite" in url:
        # aiosqlite: no pool, needs check_same_thread=False
        return create_async_engine(url, connect_args={"check_same_thread": False}, echo=False)
    # PostgreSQL via asyncpg — connection pool with health pre-ping
    return create_async_engine(url, pool_size=5, max_overflow=10, pool_pre_ping=True, echo=False)


_engine         = _make_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _logger.info("db_ready", extra={"url": settings.db_url.split("@")[-1]})
    except Exception as exc:
        _logger.error("db_init_failed", extra={"error": str(exc)})


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    await _engine.dispose()
