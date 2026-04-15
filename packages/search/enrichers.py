"""Academic metadata enrichers.

UnpaywallResolver  — given a DOI, returns an open-access PDF URL (legal OA only).
CrossRefEnricher   — given a DOI, fills in publication metadata on a SearchResult.

Both are best-effort: if the external API is unavailable or returns no data the
original SearchResult is returned unchanged.
"""
from __future__ import annotations

import httpx

from packages.common.models import SearchResult


class UnpaywallResolver:
    """Resolves DOIs to legally open-access PDF URLs via the Unpaywall API.

    Requires an email address for the polite pool (Unpaywall ToS).
    Set WISP_ACADEMIC_MAILTO in the environment.
    """

    BASE = "https://api.unpaywall.org/v2"

    def __init__(self, email: str, timeout: int = 10):
        self.email = email
        self.timeout = timeout

    async def resolve(self, doi: str) -> str | None:
        """Return an OA PDF URL for the DOI, or None if not available."""
        if not self.email or not doi:
            return None
        url = f"{self.BASE}/{doi}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                r = await client.get(url, params={"email": self.email})
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                data = r.json()
            except Exception:
                return None

        if not data.get("is_oa"):
            return None
        best = data.get("best_oa_location") or {}
        return best.get("url_for_pdf") or best.get("url")

    async def enrich(self, result: SearchResult) -> SearchResult:
        """Populate result.oa_pdf_url from Unpaywall if not already set."""
        if result.oa_pdf_url or not result.doi:
            return result
        pdf_url = await self.resolve(result.doi)
        if pdf_url:
            result.oa_pdf_url = pdf_url
        return result


class CrossRefEnricher:
    """Fills publication metadata from CrossRef for results that have a DOI."""

    BASE = "https://api.crossref.org/works"

    def __init__(self, mailto: str = "", timeout: int = 10):
        self.mailto = mailto
        self.timeout = timeout

    async def enrich(self, result: SearchResult) -> SearchResult:
        if not result.doi:
            return result
        params = {}
        if self.mailto:
            params["mailto"] = self.mailto
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                r = await client.get(f"{self.BASE}/{result.doi}", params=params)
                if r.status_code == 404:
                    return result
                r.raise_for_status()
                msg = r.json().get("message", {})
            except Exception:
                return result

        if not result.authors:
            result.authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in msg.get("author", [])
                if a.get("family")
            ][:5]
        if not result.publication_year:
            date_parts = (msg.get("published-print") or msg.get("published-online") or {}).get("date-parts", [[]])
            if date_parts and date_parts[0]:
                result.publication_year = date_parts[0][0]
        return result
