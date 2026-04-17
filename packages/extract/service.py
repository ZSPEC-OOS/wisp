from __future__ import annotations

import asyncio
import io
import logging
import time
from datetime import datetime

import httpx
import trafilatura
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from packages.common.models import ExtractedDocument, Passage
from packages.common.url import canonicalize_url

_EXTRACT_SEMAPHORE = asyncio.Semaphore(5)
_logger = logging.getLogger("wisp.extract")

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


def _shingle(text: str, k: int = 6) -> frozenset[str]:
    """Return a set of k-word shingles for Jaccard-based near-dedup."""
    words = text.lower().split()
    if len(words) <= k:
        return frozenset([" ".join(words)])
    return frozenset(" ".join(words[i : i + k]) for i in range(len(words) - k + 1))


def _dedup_passages(passages: list[Passage], threshold: float = 0.5) -> list[Passage]:
    """Remove near-duplicate passages using Jaccard similarity on word shingles."""
    kept_shingles: list[frozenset[str]] = []
    out: list[Passage] = []
    for p in passages:
        text = p.text.strip()
        shingles = _shingle(text)
        duplicate = False
        for kept in kept_shingles:
            union = shingles | kept
            if union and len(shingles & kept) / len(union) >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept_shingles.append(shingles)
            out.append(p)
    return out


class ExtractService:
    def __init__(self, user_agent: str, timeout_seconds: int = 12):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
    async def _fetch(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        response = await client.get(url)
        # Treat transient server errors as retryable
        if response.status_code in {429, 503}:
            raise httpx.HTTPStatusError(
                f"retryable status {response.status_code}",
                request=response.request,
                response=response,
            )
        return response

    async def extract_url(self, url: str, format: str = "markdown", include_images: bool = False) -> ExtractedDocument:
        headers = {"User-Agent": self.user_agent}
        t0 = time.perf_counter()
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

            raw_passages = [
                Passage(text=p.strip(), source_url=response.url)
                for p in (md or "").split("\n\n")
                if len(p.strip()) > 40
            ]
            passages = _dedup_passages(raw_passages)[:20]

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

            _logger.debug(
                "extracted",
                extra={
                    "url": url,
                    "status": "ok",
                    "passages": len(passages),
                    "content_length": len(response.content),
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "is_pdf": is_pdf,
                },
            )
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
            _logger.warning(
                "extract_failed",
                extra={"url": url, "error": str(exc), "duration_ms": round((time.perf_counter() - t0) * 1000, 1)},
            )
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
