"""Open-access academic search providers.

Each implements the SearchProvider ABC and populates the academic
fields on SearchResult (doi, authors, citation_count, publication_year,
oa_pdf_url).  All three providers are free; Semantic Scholar optionally
accepts an API key for higher rate limits.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from packages.common.models import SearchResult
from packages.common.url import domain_of
from packages.search.providers import SearchProvider


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_doi(raw: str | None) -> str | None:
    """Normalise DOI to bare form e.g. '10.1234/foo' (strip URL prefix)."""
    if not raw:
        return None
    return raw.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

class OpenAlexProvider(SearchProvider):
    """Full-spectrum academic graph — CC0, no API key required."""

    name = "openalex"

    def __init__(self, mailto: str = "", timeout: int = 15, per_page: int = 4):
        self.mailto = mailto
        self.timeout = timeout
        self.per_page = per_page

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        params: dict = {
            "search": query,
            "per-page": min(max_results, self.per_page),
            "sort": "relevance_score:desc",
        }
        if self.mailto:
            params["mailto"] = self.mailto

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                r = await client.get("https://api.openalex.org/works", params=params)
                r.raise_for_status()
                data = r.json()
            except Exception:
                return []

        results = []
        for i, work in enumerate(data.get("results", [])[:max_results], start=1):
            doi = _clean_doi(work.get("doi"))
            # Prefer landing page URL; fall back to OpenAlex page
            url = (
                (work.get("primary_location") or {}).get("landing_page_url")
                or (work.get("open_access") or {}).get("oa_url")
                or work.get("id")
            )
            if not url or not url.startswith("http"):
                continue
            authors = [
                a["author"]["display_name"]
                for a in work.get("authorships", [])
                if a.get("author", {}).get("display_name")
            ]
            oa_url = (work.get("open_access") or {}).get("oa_url")
            abstract_inv = work.get("abstract_inverted_index") or {}
            # Reconstruct abstract from inverted index
            if abstract_inv:
                words: list[str] = [""] * (max(max(v) for v in abstract_inv.values()) + 1)
                for word, positions in abstract_inv.items():
                    for pos in positions:
                        words[pos] = word
                snippet = " ".join(words)[:300]
            else:
                snippet = work.get("title") or ""
            results.append(
                SearchResult(
                    title=work.get("title") or url,
                    url=url,
                    snippet=snippet,
                    source_domain=domain_of(url),
                    rank=i,
                    provider=self.name,
                    retrieved_at=_now(),
                    doi=doi,
                    authors=authors[:5],
                    citation_count=work.get("cited_by_count"),
                    publication_year=work.get("publication_year"),
                    oa_pdf_url=oa_url,
                )
            )
        return results


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

class ArxivProvider(SearchProvider):
    """arXiv preprint server — physics, CS, mathematics, biology. No key needed."""

    name = "arxiv"

    def __init__(self, timeout: int = 15, max_results: int = 4):
        self.timeout = timeout
        self._max = max_results

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        params = {
            "search_query": f"all:{query}",
            "max_results": min(max_results, self._max),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                r = await client.get("http://export.arxiv.org/api/query", params=params)
                r.raise_for_status()
            except Exception:
                return []

        soup = BeautifulSoup(r.text, "lxml-xml")
        results = []
        for i, entry in enumerate(soup.find_all("entry")[:max_results], start=1):
            raw_id = entry.find("id")
            if not raw_id:
                continue
            abs_url = raw_id.text.strip()
            # Convert PDF link to abs page for HTML extraction
            pdf_link = entry.find("link", attrs={"type": "application/pdf"})
            oa_pdf_url = pdf_link["href"] if pdf_link else abs_url.replace("/abs/", "/pdf/")

            doi_tag = entry.find("arxiv:doi")
            doi = doi_tag.text.strip() if doi_tag else None

            title_tag = entry.find("title")
            title = title_tag.text.strip() if title_tag else abs_url

            summary_tag = entry.find("summary")
            snippet = summary_tag.text.strip()[:300] if summary_tag else ""

            authors = [a.find("name").text.strip() for a in entry.find_all("author") if a.find("name")]

            results.append(
                SearchResult(
                    title=title,
                    url=abs_url,
                    snippet=snippet,
                    source_domain="arxiv.org",
                    rank=i,
                    provider=self.name,
                    retrieved_at=_now(),
                    doi=doi,
                    authors=authors[:5],
                    oa_pdf_url=oa_pdf_url,
                )
            )
        return results


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

class SemanticScholarProvider(SearchProvider):
    """AI-enhanced literature search with citation graph. Optional API key."""

    name = "semantic_scholar"

    _FIELDS = "title,authors,year,abstract,citationCount,openAccessPdf,externalIds"

    def __init__(self, api_key: str = "", timeout: int = 15, limit: int = 4):
        self.api_key = api_key
        self.timeout = timeout
        self.limit = limit

    async def search(self, query: str, max_results: int = 10, topic: str = "general") -> list[SearchResult]:
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        params = {
            "query": query,
            "fields": self._FIELDS,
            "limit": min(max_results, self.limit),
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            try:
                r = await client.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
            except Exception:
                return []

        results = []
        for i, paper in enumerate(data.get("data", [])[:max_results], start=1):
            doi = _clean_doi((paper.get("externalIds") or {}).get("DOI"))
            oa_info = paper.get("openAccessPdf") or {}
            oa_pdf_url = oa_info.get("url")

            # Build a stable URL — prefer DOI resolver, fall back to S2 page
            if doi:
                url = f"https://doi.org/{doi}"
            else:
                url = f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}"

            authors = [a["name"] for a in paper.get("authors", []) if a.get("name")]
            snippet = (paper.get("abstract") or "")[:300]

            results.append(
                SearchResult(
                    title=paper.get("title") or url,
                    url=url,
                    snippet=snippet,
                    source_domain=domain_of(url),
                    rank=i,
                    provider=self.name,
                    retrieved_at=_now(),
                    doi=doi,
                    authors=authors[:5],
                    citation_count=paper.get("citationCount"),
                    publication_year=paper.get("year"),
                    oa_pdf_url=oa_pdf_url,
                )
            )
        return results
