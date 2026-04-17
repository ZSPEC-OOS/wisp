from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from packages.common.models import SearchResult
from packages.common.url import domain_of

_logger = logging.getLogger("wisp.search")


_TOPIC_SITE_FILTERS: dict[str, str] = {
    "news": "site:reuters.com OR site:bbc.com OR site:apnews.com OR site:theguardian.com",
    "finance": "site:finance.yahoo.com OR site:bloomberg.com OR site:marketwatch.com OR site:ft.com",
    "academic": "site:arxiv.org OR site:scholar.google.com OR site:semanticscholar.org OR site:pubmed.ncbi.nlm.nih.gov",
}


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        raise NotImplementedError


class DuckDuckGoProvider(SearchProvider):
    name = "duckduckgo"

    def __init__(self, timeout_seconds: int = 12):
        self.timeout_seconds = timeout_seconds

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, max=3))
    async def _call_api(self, client: httpx.AsyncClient, url: str, params: dict) -> dict:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        site_filter = _TOPIC_SITE_FILTERS.get(topic)
        effective_query = f"{query} ({site_filter})" if site_filter else query
        url = "https://api.duckduckgo.com/"
        params = {"q": effective_query, "format": "json", "no_html": 1, "skip_disambig": 1}

        rows = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            try:
                data = await self._call_api(client, url, params)
            except Exception as exc:
                _logger.warning("ddg_api_failed", extra={"query": query, "error": str(exc)})
                data = {}

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

        if not rows:
            # Fallback: hit DDG HTML search endpoint and parse result links
            html_url = "https://html.duckduckgo.com/html/"
            html_params = {"q": effective_query}
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; WISPBot/0.1)"},
                ) as html_client:
                    html_resp = await html_client.post(html_url, data=html_params)
                    html_resp.raise_for_status()
                soup = BeautifulSoup(html_resp.text, "lxml")
                for i, result_div in enumerate(soup.select("div.result__body")[:max_results], start=1):
                    a_tag = result_div.select_one("a.result__a")
                    snippet_tag = result_div.select_one("a.result__snippet")
                    if not a_tag:
                        continue
                    raw_url = a_tag.get("href", "")
                    if not raw_url.startswith("http"):
                        continue
                    title = a_tag.get_text(strip=True)
                    snippet = snippet_tag.get_text(strip=True) if snippet_tag else title
                    rows.append(
                        SearchResult(
                            title=title,
                            url=raw_url,
                            snippet=snippet,
                            source_domain=domain_of(raw_url),
                            rank=i,
                            provider=self.name,
                            retrieved_at=datetime.now(timezone.utc),
                        )
                    )
            except Exception as exc:
                _logger.warning("ddg_html_fallback_failed", extra={"query": query, "error": str(exc)})

        return rows


class SearXNGProvider(SearchProvider):
    """Self-hosted SearXNG instance — aggregates many engines, no API key needed."""

    name = "searxng"

    def __init__(self, base_url: str, timeout_seconds: int = 12):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        category = {"news": "news", "academic": "science", "finance": "general"}.get(topic, "general")
        params = {"q": query, "format": "json", "categories": category, "pageno": 1}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                r = await client.get(f"{self.base_url}/search", params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            _logger.warning("searxng_search_failed", extra={"query": query, "url": self.base_url, "error": str(exc)})
            return []

        results = []
        now = datetime.now(timezone.utc)
        for i, item in enumerate(data.get("results", [])[:max_results], start=1):
            url = item.get("url", "")
            if not url:
                continue
            published_date = None
            raw_date = item.get("publishedDate") or item.get("published_date")
            if raw_date:
                try:
                    published_date = datetime.fromisoformat(raw_date.rstrip("Z")).replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    pass
            results.append(SearchResult(
                title=item.get("title", url),
                url=url,
                snippet=(item.get("content") or "")[:300],
                source_domain=domain_of(url),
                rank=i,
                provider=self.name,
                retrieved_at=now,
                published_date=published_date,
            ))
        return results
