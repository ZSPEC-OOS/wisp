from __future__ import annotations

from datetime import datetime, timedelta, timezone


class TTLCache:
    def __init__(self, ttl_seconds: int = 900, max_size: int = 1000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._data: dict[str, tuple[datetime, object]] = {}

    def get(self, key: str):
        if key not in self._data:
            return None
        expires_at, value = self._data[key]
        if expires_at < datetime.now(timezone.utc):
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl: int | None = None) -> None:
        lifetime = ttl if ttl is not None else self.ttl
        # Evict oldest entry when at capacity
        if len(self._data) >= self.max_size and key not in self._data:
            oldest_key = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest_key]
        self._data[key] = (datetime.now(timezone.utc) + timedelta(seconds=lifetime), value)

    def invalidate(self, prefix: str | None = None) -> None:
        if not prefix:
            self._data.clear()
            return
        for key in list(self._data.keys()):
            if key.startswith(prefix):
                del self._data[key]

    def stats(self) -> dict:
        return {"size": len(self._data), "max_size": self.max_size}
