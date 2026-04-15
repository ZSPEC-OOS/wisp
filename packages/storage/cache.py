from __future__ import annotations

from datetime import datetime, timedelta


class TTLCache:
    def __init__(self, ttl_seconds: int = 900):
        self.ttl = ttl_seconds
        self._data: dict[str, tuple[datetime, object]] = {}

    def get(self, key: str):
        if key not in self._data:
            return None
        expires_at, value = self._data[key]
        if expires_at < datetime.utcnow():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl: int | None = None) -> None:
        lifetime = ttl if ttl is not None else self.ttl
        self._data[key] = (datetime.utcnow() + timedelta(seconds=lifetime), value)

    def invalidate(self, prefix: str | None = None) -> None:
        if not prefix:
            self._data.clear()
            return
        for key in list(self._data.keys()):
            if key.startswith(prefix):
                del self._data[key]
