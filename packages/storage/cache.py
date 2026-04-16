from __future__ import annotations

from datetime import datetime, timedelta, timezone


class TTLCache:
    def __init__(self, ttl_seconds: int = 900, max_size: int = 1000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._data: dict[str, tuple[datetime, object]] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _prune_expired(self) -> int:
        now = datetime.now(timezone.utc)
        pruned = 0
        for key, (expires_at, _) in list(self._data.items()):
            if expires_at < now:
                del self._data[key]
                pruned += 1
        return pruned

    def get(self, key: str):
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

    def set(self, key: str, value: object, ttl: int | None = None) -> None:
        lifetime = ttl if ttl is not None else self.ttl
        # Evict oldest entry when at capacity
        if len(self._data) >= self.max_size and key not in self._data:
            oldest_key = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest_key]
            self._evictions += 1
        self._data[key] = (datetime.now(timezone.utc) + timedelta(seconds=lifetime), value)

    def invalidate(self, prefix: str | None = None) -> None:
        if not prefix:
            self._data.clear()
            return
        for key in list(self._data.keys()):
            if key.startswith(prefix):
                del self._data[key]

    def stats(self) -> dict:
        self._prune_expired()
        return {
            "size": len(self._data),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "hit_rate": round(self._hits / max(1, self._hits + self._misses), 4),
        }
