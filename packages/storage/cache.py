from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime, timedelta, timezone
from typing import Any


class TTLCache:
    """In-process TTL cache. Fast but not shared across workers."""

    def __init__(self, ttl_seconds: int = 900, max_size: int = 1000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._data: dict[str, tuple[datetime, object]] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    async def get(self, key: str):
        if key not in self._data:
            self._misses += 1
            return None
        expires_at, value = self._data[key]
        if expires_at < datetime.now(timezone.utc):
            self._data.pop(key, None)
            self._misses += 1
            return None
        self._hits += 1
        return value

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        lifetime = ttl if ttl is not None else self.ttl
        if len(self._data) >= self.max_size and key not in self._data:
            oldest_key = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest_key]
            self._evictions += 1
        self._data[key] = (datetime.now(timezone.utc) + timedelta(seconds=lifetime), value)

    async def invalidate(self, prefix: str | None = None) -> None:
        if not prefix:
            self._data.clear()
            return
        for key in list(self._data.keys()):
            if key.startswith(prefix):
                del self._data[key]

    async def _prune_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired = [k for k, (exp, _) in self._data.items() if exp < now]
        for k in expired:
            del self._data[k]
        return len(expired)

    async def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._data),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "hit_rate": round(self._hits / max(1, total), 4),
        }

    async def aclose(self) -> None:
        pass


class RedisTTLCache:
    """Distributed TTL cache backed by Redis. Drop-in replacement for TTLCache.

    Values are JSON-serialised with SETEX. Prefix-based invalidation uses SCAN+DEL.
    All errors degrade gracefully to cache misses so Redis unavailability never
    breaks API responses.
    """

    def __init__(self, redis_url: str, ttl_seconds: int = 900, key_prefix: str = "wisp:cache:"):
        self._redis_url = redis_url
        self.ttl = ttl_seconds
        self.max_size = 0  # Redis enforces memory limits via maxmemory policy
        self._prefix = key_prefix
        self._redis: Any = None
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                import redis.asyncio as aioredis
                self._redis = await aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
        return self._redis

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str):
        try:
            r = await self._get_redis()
            raw = await r.get(self._k(key))
            if raw is None:
                self._misses += 1
                return None
            self._hits += 1
            return _json.loads(raw)
        except Exception:
            self._misses += 1
            return None

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        try:
            r = await self._get_redis()
            lifetime = ttl if ttl is not None else self.ttl
            await r.setex(self._k(key), lifetime, _json.dumps(value, default=str))
        except Exception:
            pass

    async def invalidate(self, prefix: str | None = None) -> None:
        try:
            r = await self._get_redis()
            pattern = f"{self._prefix}{prefix or ''}*"
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=200)
                if keys:
                    await r.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    async def _prune_expired(self) -> int:
        return 0  # Redis handles expiry natively via SETEX TTLs

    async def stats(self) -> dict:
        total = self._hits + self._misses
        size = 0
        try:
            r = await self._get_redis()
            # Count only our namespace keys via SCAN
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match=f"{self._prefix}*", count=200)
                size += len(keys)
                if cursor == 0:
                    break
        except Exception:
            pass
        return {
            "size": size,
            "max_size": 0,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "hit_rate": round(self._hits / max(1, total), 4),
        }

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
