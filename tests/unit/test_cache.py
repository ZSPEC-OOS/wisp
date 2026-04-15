import time

from packages.storage.cache import TTLCache


def test_cache_expiry():
    c = TTLCache(ttl_seconds=1)
    c.set("k", 1)
    assert c.get("k") == 1
    time.sleep(1.1)
    assert c.get("k") is None
