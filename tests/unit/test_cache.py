from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.storage.cache import TTLCache


async def test_set_and_get():
    c = TTLCache(ttl_seconds=60)
    await c.set("k", "value")
    assert await c.get("k") == "value"


async def test_miss_returns_none():
    c = TTLCache(ttl_seconds=60)
    assert await c.get("no_such_key") is None


async def test_expiry_via_manipulated_timestamp():
    c = TTLCache(ttl_seconds=60)
    await c.set("k", 42)
    # Back-date the expiry so it appears stale
    c._data["k"] = (datetime.now(timezone.utc) - timedelta(seconds=1), 42)
    assert await c.get("k") is None


async def test_custom_ttl_overrides_default():
    c = TTLCache(ttl_seconds=60)
    await c.set("k", "v", ttl=300)
    exp, _ = c._data["k"]
    remaining = (exp - datetime.now(timezone.utc)).total_seconds()
    assert 290 < remaining <= 300


async def test_invalidate_all():
    c = TTLCache(ttl_seconds=60)
    await c.set("a", 1)
    await c.set("b", 2)
    await c.invalidate()
    assert await c.get("a") is None
    assert await c.get("b") is None


async def test_invalidate_prefix():
    c = TTLCache(ttl_seconds=60)
    await c.set("prefix:x", 1)
    await c.set("prefix:y", 2)
    await c.set("other:z", 3)
    await c.invalidate(prefix="prefix:")
    assert await c.get("prefix:x") is None
    assert await c.get("prefix:y") is None
    assert await c.get("other:z") == 3


async def test_prune_expired_removes_stale_entries():
    c = TTLCache(ttl_seconds=60)
    await c.set("stale", "x")
    await c.set("fresh", "y")
    c._data["stale"] = (datetime.now(timezone.utc) - timedelta(seconds=1), "x")
    pruned = await c._prune_expired()
    assert pruned == 1
    assert "stale" not in c._data
    assert "fresh" in c._data


async def test_stats_hit_rate():
    c = TTLCache(ttl_seconds=60)
    await c.set("k", 1)
    await c.get("k")
    await c.get("missing")
    s = await c.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 0.5


async def test_eviction_when_full():
    c = TTLCache(ttl_seconds=60, max_size=2)
    await c.set("a", 1)
    await c.set("b", 2)
    await c.set("c", 3)  # triggers eviction
    s = await c.stats()
    assert s["size"] == 2
    assert s["evictions"] == 1


async def test_overwrite_same_key_does_not_evict():
    c = TTLCache(ttl_seconds=60, max_size=2)
    await c.set("a", 1)
    await c.set("b", 2)
    await c.set("a", 99)  # same key, should not evict
    s = await c.stats()
    assert s["evictions"] == 0
    assert await c.get("a") == 99
