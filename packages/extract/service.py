from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import trafilatura
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from packages.common.models import ExtractedDocument, Passage
from packages.common.url import canonicalize_url

_EXTRACT_SEMAPHORE = asyncio.Semaphore(5)


class ExtractService:
    def __init__(self, user_agent: str, timeout_seconds: int = 12):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
    async def _fetch(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        return await client.get(url)

    async def extract_url(self, url: str, format: str = "markdown", include_images: bool = False) -> ExtractedDocument:
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers, follow_redirects=True) as client:
                response = await self._fetch(client, url)
            content_type = response.headers.get("content-type", "")
            body = response.text
            md = trafilatura.extract(
                body,
                output_format="markdown" if format == "markdown" else "txt",
                include_images=include_images,
                include_comments=False,
                include_tables=True,
            )
            if not md:
                soup = BeautifulSoup(body, "lxml")
                md = soup.get_text("\n", strip=True)
            passages = [
                Passage(text=p.strip(), source_url=response.url)
                for p in md.split("\n\n")
                if len(p.strip()) > 40
            ][:20]
            title = trafilatura.extract_metadata(body).title if trafilatura.extract_metadata(body) else None
            published = None
            if trafilatura.extract_metadata(body) and trafilatura.extract_metadata(body).date:
                published = datetime.fromisoformat(trafilatura.extract_metadata(body).date)
            return ExtractedDocument(
                url=url,
                canonical_url=canonicalize_url(str(response.url)),
                title=title,
                author=trafilatura.extract_metadata(body).author if trafilatura.extract_metadata(body) else None,
                published_at=published,
                status="ok",
                format="markdown" if format == "markdown" else "text",
                content=md,
                passages=passages,
                diagnostics={"content_type": content_type, "length": len(body)},
            )
        except Exception as exc:
            return ExtractedDocument(
                url=url,
                canonical_url=canonicalize_url(url),
                status="error",
                format="markdown" if format == "markdown" else "text",
                content="",
                passages=[],
                diagnostics={"error": str(exc)},
            )

    async def _extract_url_with_semaphore(self, url: str, format: str, include_images: bool) -> ExtractedDocument:
        async with _EXTRACT_SEMAPHORE:
            return await self.extract_url(url, format=format, include_images=include_images)

    async def extract_many(self, urls: list[str], format: str = "markdown", include_images: bool = False) -> list[ExtractedDocument]:
        return list(
            await asyncio.gather(
                *[self._extract_url_with_semaphore(url, format=format, include_images=include_images) for url in urls]
            )
        )
