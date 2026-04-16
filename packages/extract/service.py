from __future__ import annotations

import asyncio
import io
from datetime import datetime

import httpx
import trafilatura
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from packages.common.models import ExtractedDocument, Passage
from packages.common.url import canonicalize_url

_EXTRACT_SEMAPHORE = asyncio.Semaphore(5)

try:
    import pypdf  # optional — add pypdf to dependencies for PDF support
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False


def _extract_pdf_text(raw_bytes: bytes) -> str:
    """Extract plain text from a PDF byte payload using pypdf."""
    reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _dedup_passages(passages: list[Passage]) -> list[Passage]:
    seen: list[str] = []
    out = []
    for p in passages:
        text = p.text.strip()
        # Skip if text is a substring of any already-kept passage, or vice-versa
        if any(text in s or s in text for s in seen):
            continue
        seen.append(text)
        out.append(p)
    return out


class ExtractService:
    def __init__(self, user_agent: str, timeout_seconds: int = 12):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
    async def _fetch(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        response = await client.get(url)
        if response.status_code in {429, 503}:
            raise httpx.HTTPStatusError(
                f"retryable status {response.status_code}",
                request=response.request,
                response=response,
            )
        return response

    async def extract_url(self, url: str, format: str = "markdown", include_images: bool = False) -> ExtractedDocument:
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers, follow_redirects=True) as client:
                response = await self._fetch(client, url)
            content_type = response.headers.get("content-type", "")
            is_pdf = "application/pdf" in content_type or url.lower().endswith(".pdf")

            if is_pdf and _PYPDF_AVAILABLE:
                md = _extract_pdf_text(response.content)
            elif is_pdf:
                # pypdf not installed — fall back to the arXiv abstract page if possible
                if "arxiv.org/pdf" in url:
                    url = url.replace("/pdf/", "/abs/").removesuffix(".pdf")
                    async with httpx.AsyncClient(
                        timeout=self.timeout_seconds, headers=headers, follow_redirects=True
                    ) as client2:
                        response = await self._fetch(client2, url)
                body = response.text
                md = trafilatura.extract(body, output_format="markdown" if format == "markdown" else "txt",
                                         include_images=include_images, include_comments=False, include_tables=True)
                if not md:
                    soup = BeautifulSoup(body, "lxml")
                    md = soup.get_text("\n", strip=True)
            else:
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
                for p in (md or "").split("\n\n")
                if len(p.strip()) > 40
            ]
            passages = _dedup_passages(passages)[:20]
            # Metadata extraction only applies to HTML content
            html_body = response.text if not is_pdf else None
            meta = trafilatura.extract_metadata(html_body) if html_body else None
            title = meta.title if meta else None
            published = None
            if meta and meta.date:
                try:
                    published = datetime.fromisoformat(meta.date)
                except ValueError:
                    pass
            return ExtractedDocument(
                url=url,
                canonical_url=canonicalize_url(str(response.url)),
                title=title,
                author=meta.author if meta else None,
                published_at=published,
                status="ok",
                format="markdown" if format == "markdown" else "text",
                content=md or "",
                passages=passages,
                diagnostics={"content_type": content_type, "length": len(response.content), "pdf": is_pdf},
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
