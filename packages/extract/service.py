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
        # Shared persistent client — reuses TCP connections across requests.
        # Capped pool prevents fd exhaustion under concurrent crawl + extract load.
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(connect=5.0, read=float(timeout_seconds), write=10.0, pool=5.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _playwright_extract(self, url: str) -> str | None:
        """Render URL with headless Chromium; returns raw HTML or None if unavailable."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(
                        user_agent=self.user_agent,
                        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    )
                    await page.goto(url, wait_until="networkidle", timeout=20_000)
                    html = await page.content()
                    return html
                finally:
                    await browser.close()
        except Exception as exc:
            _logger.warning("playwright_extract_failed", extra={"url": url, "error": str(exc)})
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
    async def _fetch(self, url: str) -> httpx.Response:
        response = await self._client.get(url)
        # Treat transient server errors as retryable
        if response.status_code in {429, 503}:
            raise httpx.HTTPStatusError(
                f"retryable status {response.status_code}",
                request=response.request,
                response=response,
            )
        return response

    async def extract_url(self, url: str, format: str = "markdown", include_images: bool = False, js_render: bool = False) -> ExtractedDocument:
        t0 = time.perf_counter()
        try:
            response = await self._fetch(url)
            content_type = response.headers.get("content-type", "")
            is_pdf = "application/pdf" in content_type or url.lower().endswith(".pdf")

            # JS rendering: after detecting content type, re-fetch with Playwright for
            # dynamically-rendered pages (React, Vue, etc.) that httpx cannot see.
            if js_render and not is_pdf:
                rendered = await self._playwright_extract(url)
                if rendered:
                    body = rendered
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
                    meta = trafilatura.extract_metadata(body)
                    title = meta.title if meta else None
                    published = None
                    if meta and meta.date:
                        try:
                            published = datetime.fromisoformat(meta.date)
                        except ValueError:
                            pass
                    _logger.debug("extracted_js", extra={"url": url, "passages": len(passages),
                                                          "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})
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
                        diagnostics={"content_type": content_type, "length": len(body), "js_render": True},
                    )

            if is_pdf and _PYPDF_AVAILABLE:
                md = _extract_pdf_text(response.content)
            elif is_pdf:
                # pypdf not installed — fall back to the arXiv abstract page if possible
                if "arxiv.org/pdf" in url:
                    url = url.replace("/pdf/", "/abs/").removesuffix(".pdf")
                    response = await self._fetch(url)
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

    async def _extract_url_with_semaphore(self, url: str, format: str, include_images: bool, js_render: bool) -> ExtractedDocument:
        async with _EXTRACT_SEMAPHORE:
            return await self.extract_url(url, format=format, include_images=include_images, js_render=js_render)

    async def extract_many(self, urls: list[str], format: str = "markdown", include_images: bool = False, js_render: bool = False) -> list[ExtractedDocument]:
        return list(
            await asyncio.gather(
                *[self._extract_url_with_semaphore(url, format=format, include_images=include_images, js_render=js_render) for url in urls]
            )
        )
