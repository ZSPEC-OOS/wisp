from __future__ import annotations

import asyncio
import math
import time

from fastapi import Header, HTTPException, Request, status

from apps.api.config import settings


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
        """Return seconds until the next token becomes available."""
        if self.refill_rate <= 0:
            return 60.0
        return (1.0 - self.tokens) / self.refill_rate


class _TokenBucketLimiter:
    """Per-process in-memory token bucket rate limiter.

    NOTE: Each worker process has an independent bucket. Under uvicorn
    --workers N the effective limit becomes N * rate_limit_per_minute.
    For multi-worker deployments, replace with a Redis-backed backend.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    def _get_bucket(self, key: str) -> _Bucket:
        if key not in self._buckets:
            rpm = settings.rate_limit_per_minute
            self._buckets[key] = _Bucket(capacity=float(rpm), refill_rate=rpm / 60.0)
        return self._buckets[key]

    async def check(self, key: str) -> None:
        async with self._lock:
            bucket = self._get_bucket(key)
            if not bucket.consume():
                retry_after = str(max(1, math.ceil(bucket.time_to_next_token())))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="rate_limit_exceeded",
                    headers={"Retry-After": retry_after},
                )


_limiter = _TokenBucketLimiter()


async def require_rate_limit(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """FastAPI dependency. No-op when rate_limit_per_minute == 0."""
    if not settings.rate_limit_per_minute:
        return
    key = x_api_key or (request.client.host if request.client else "anonymous")
    await _limiter.check(key)
