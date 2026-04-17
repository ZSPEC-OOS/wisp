from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from fastapi import Header, HTTPException, Request, status

from apps.api.config import settings


# ── In-process token bucket (single-worker fallback) ────────────────────────

class _Bucket:
    __slots__ = ("capacity", "tokens", "refill_rate", "last_refill")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def time_to_next_token(self) -> float:
        if self.refill_rate <= 0:
            return 60.0
        return (1.0 - self.tokens) / self.refill_rate


class _InMemoryLimiter:
    """Per-process token bucket. Accurate only for single-worker deployments."""

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    def _get_bucket(self, key: str, rpm: int) -> _Bucket:
        if key not in self._buckets:
            self._buckets[key] = _Bucket(capacity=float(rpm), refill_rate=rpm / 60.0)
        return self._buckets[key]

    async def check(self, key: str, rpm: int) -> None:
        async with self._lock:
            bucket = self._get_bucket(key, rpm)
            if not bucket.consume():
                retry_after = str(max(1, math.ceil(bucket.time_to_next_token())))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="rate_limit_exceeded",
                    headers={"Retry-After": retry_after},
                )

    async def aclose(self) -> None:
        pass


# ── Redis sliding-window rate limiter (multi-worker safe) ────────────────────

class _RedisLimiter:
    """Fixed-window rate limiter backed by Redis.

    Uses INCR + EXPIRE per (identifier, minute-bucket) key.  Accurate across
    any number of workers sharing the same Redis instance.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any = None
        self._lock = asyncio.Lock()
        self._failure_streak: int = 0

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                import redis.asyncio as aioredis  # optional dep
                self._redis = await aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
        return self._redis

    async def check(self, key: str, rpm: int) -> None:
        try:
            r = await self._get_redis()
            now = time.time()
            minute_bucket = int(now // 60)
            redis_key = f"rl:{key}:{minute_bucket}"
            count = await r.incr(redis_key)
            if count == 1:
                await r.expire(redis_key, 120)  # 2-minute TTL covers boundary bursts
            if count > rpm:
                seconds_left = 60 - int(now % 60)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="rate_limit_exceeded",
                    headers={"Retry-After": str(max(1, seconds_left))},
                )
            self._failure_streak = 0
        except HTTPException:
            raise
        except Exception:
            # Redis unavailable — degrade gracefully; force reconnect after 3 consecutive failures
            self._failure_streak += 1
            if self._failure_streak >= 3:
                self._redis = None
                self._failure_streak = 0

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()


# ── Limiter selection ────────────────────────────────────────────────────────

_limiter: _InMemoryLimiter | _RedisLimiter

def _build_limiter() -> _InMemoryLimiter | _RedisLimiter:
    if settings.redis_url:
        return _RedisLimiter(settings.redis_url)
    return _InMemoryLimiter()


_limiter = _build_limiter()


# ── FastAPI dependencies ─────────────────────────────────────────────────────

async def require_rate_limit(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Global rate limit dependency. No-op when rate_limit_per_minute == 0."""
    if not settings.rate_limit_per_minute:
        return
    key = x_api_key or (request.client.host if request.client else "anonymous")
    await _limiter.check(key, settings.rate_limit_per_minute)


def make_rate_limit_dep(endpoint_rpm_getter):
    """Factory for per-endpoint rate limit dependencies."""
    async def dep(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> None:
        rpm = endpoint_rpm_getter() or settings.rate_limit_per_minute
        if not rpm:
            return
        key = f"{x_api_key or (request.client.host if request.client else 'anonymous')}:{request.url.path}"
        await _limiter.check(key, rpm)
    return dep
