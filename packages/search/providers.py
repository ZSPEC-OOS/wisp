from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from packages.common.models import SearchResult
from packages.common.url import domain_of


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

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        # Free DDG instant answer endpoint; related topics are used as web-like hints.
        site_filter = _TOPIC_SITE_FILTERS.get(topic)
        effective_query = f"{query} ({site_filter})" if site_filter else query
        url = "https://api.duckduckgo.com/"
        params = {"q": effective_query, "format": "json", "no_html": 1, "skip_disambig": 1}
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
            except Exception:
                pass  # Best-effort; return empty list rather than crash

        return rows
