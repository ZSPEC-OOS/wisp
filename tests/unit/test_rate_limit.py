from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from apps.api.dependencies.rate_limit import _Bucket, _InMemoryLimiter, _RedisLimiter


# ── Token bucket ──────────────────────────────────────────────────────────────

def test_bucket_consume_succeeds_when_full():
    b = _Bucket(capacity=10.0, refill_rate=1.0)
    assert b.consume() is True


def test_bucket_consume_fails_when_empty():
    b = _Bucket(capacity=1.0, refill_rate=0.01)
    b.consume()  # drain the single token
    assert b.consume() is False


def test_bucket_time_to_next_token_when_empty():
    b = _Bucket(capacity=1.0, refill_rate=1.0)
    b.consume()
    t = b.time_to_next_token()
    assert t > 0.0


def test_bucket_zero_refill_rate_returns_fallback():
    b = _Bucket(capacity=1.0, refill_rate=0.0)
    assert b.time_to_next_token() == 60.0


# ── In-memory limiter ─────────────────────────────────────────────────────────

async def test_in_memory_limiter_passes_within_limit():
    limiter = _InMemoryLimiter()
    await limiter.check("key1", rpm=10)  # should not raise


async def test_in_memory_limiter_raises_429_when_exhausted():
    limiter = _InMemoryLimiter()
    # Drain all tokens
    for _ in range(5):
        try:
            await limiter.check("drain_key", rpm=5)
        except HTTPException:
            pass

    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("drain_key", rpm=5)
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


async def test_in_memory_limiter_aclose_is_safe():
    limiter = _InMemoryLimiter()
    await limiter.aclose()  # should not raise


# ── Redis limiter — failure streak reconnect ──────────────────────────────────

async def test_redis_limiter_resets_redis_after_3_failures():
    limiter = _RedisLimiter("redis://localhost:6379")
    # Inject a broken redis object that always raises
    broken = AsyncMock()
    broken.incr.side_effect = ConnectionError("down")
    broken.expire.side_effect = ConnectionError("down")
    limiter._redis = broken

    for _ in range(3):
        await limiter.check("k", rpm=100)  # each call fails silently

    # After 3 failures the redis reference should be cleared for reconnect
    assert limiter._redis is None
    assert limiter._failure_streak == 0


async def test_redis_limiter_streak_resets_on_success():
    limiter = _RedisLimiter("redis://localhost:6379")

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    limiter._redis = mock_redis
    limiter._failure_streak = 2  # pre-load partial streak

    await limiter.check("k", rpm=100)
    assert limiter._failure_streak == 0


async def test_redis_limiter_raises_429_on_limit_exceeded():
    limiter = _RedisLimiter("redis://localhost:6379")

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=101)  # over limit
    mock_redis.expire = AsyncMock(return_value=True)
    limiter._redis = mock_redis

    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("k", rpm=100)
    assert exc_info.value.status_code == 429


async def test_redis_limiter_aclose_safe_without_redis():
    limiter = _RedisLimiter("redis://localhost:6379")
    await limiter.aclose()  # redis is None — should not raise


async def test_redis_limiter_aclose_calls_redis_aclose():
    limiter = _RedisLimiter("redis://localhost:6379")
    mock_redis = AsyncMock()
    limiter._redis = mock_redis
    await limiter.aclose()
    mock_redis.aclose.assert_called_once()
