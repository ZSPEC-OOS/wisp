from datetime import datetime, timezone

from packages.common.models import SearchResult
from packages.search.pipeline import dedupe_results


def test_dedupe_results_by_canonical_url():
    base = {
        "title": "x",
        "snippet": "y",
        "source_domain": "example.com",
        "provider": "duckduckgo",
        "retrieved_at": datetime.now(timezone.utc),
        "rank": 1,
    }
    rows = [
        SearchResult(url="https://example.com/a?b=2&a=1", **base),
        SearchResult(url="https://EXAMPLE.com:443/a?a=1&b=2", **base),
    ]
    assert len(dedupe_results(rows)) == 1
