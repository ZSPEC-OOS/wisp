from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from packages.common.models import SearchResult
from packages.common.url import domain_of


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        raise NotImplementedError


class DuckDuckGoProvider(SearchProvider):
    name = "duckduckgo"

    def __init__(self, timeout_seconds: int = 12):
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        # Free DDG instant answer endpoint; related topics are used as web-like hints.
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        rows = []
        topics = data.get("RelatedTopics", [])
        flat = []
        for t in topics:
            if "Topics" in t:
                flat.extend(t["Topics"])
            else:
                flat.append(t)
        for i, item in enumerate(flat[:max_results], start=1):
            raw_url = item.get("FirstURL")
            text = item.get("Text", "")
            if not raw_url:
                continue
            title = text.split(" - ")[0] if text else raw_url
            rows.append(
                SearchResult(
                    title=title,
                    url=raw_url,
                    snippet=text,
                    source_domain=domain_of(raw_url),
                    rank=i,
                    provider=self.name,
                    retrieved_at=datetime.now(timezone.utc),
                )
            )
        return rows
