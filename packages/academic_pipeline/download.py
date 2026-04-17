"""Stage 2: Fetch PDF bytes into memory from open-access or Sci-Hub sources.

Resolution order:
  1. result.oa_pdf_url  (Unpaywall / provider — legal OA)
  2. Sci-Hub mirror list (opt-in only; never hit by default)

Nothing is written to disk; callers receive raw bytes or None.

Sci-Hub note: mirrors serve an HTML landing page whose <embed> or <iframe>
contains the actual PDF URL.  We parse that page to locate the PDF before
fetching it.  Mirrors are tried concurrently; the first valid PDF wins.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

from packages.common.models import SearchResult

_logger = logging.getLogger("wisp.academic_pipeline.download")

_PDF_MAGIC = b"%PDF"

_SCIHUB_MIRRORS = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
]


def _is_pdf(data: bytes) -> bool:
    return data[:4] == _PDF_MAGIC


def _extract_pdf_url_from_html(html: str, mirror_base: str) -> str | None:
    """Parse a Sci-Hub landing page and return the embedded PDF URL."""
    soup = BeautifulSoup(html, "lxml")
    for tag in ("embed", "iframe"):
        el = soup.find(tag, src=True)
        if el:
            src: str = el["src"]
            # Scheme-relative → absolute
            if src.startswith("//"):
                src = "https:" + src
            # Relative → absolute using mirror base
            elif src.startswith("/"):
                src = mirror_base + src
            return src
    return None


async def _fetch_direct(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
        if _is_pdf(r.content):
            return r.content
        _logger.debug("not_pdf url=%s ct=%s", url, r.headers.get("content-type"))
    except Exception as exc:
        _logger.debug("fetch_failed url=%s err=%s", url, exc)
    return None


async def _scihub_one_mirror(client: httpx.AsyncClient, mirror: str, doi: str) -> bytes | None:
    landing_url = f"{mirror}/{doi}"
    try:
        r = await client.get(landing_url, follow_redirects=True)
        r.raise_for_status()
    except Exception as exc:
        _logger.debug("scihub_mirror_failed mirror=%s err=%s", mirror, exc)
        return None

    if _is_pdf(r.content):
        return r.content

    # Parse the HTML landing page for the embedded PDF URL
    pdf_url = _extract_pdf_url_from_html(r.text, mirror)
    if not pdf_url:
        _logger.debug("scihub_no_pdf_url mirror=%s doi=%s", mirror, doi)
        return None

    return await _fetch_direct(client, pdf_url)


async def _scihub_fetch(client: httpx.AsyncClient, doi: str) -> bytes | None:
    """Try all mirrors concurrently; return the first valid PDF bytes."""
    tasks = [asyncio.create_task(_scihub_one_mirror(client, m, doi)) for m in _SCIHUB_MIRRORS]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        if result is not None:
            for t in tasks:
                t.cancel()
            return result
    return None


class PdfFetcher:
    """Fetch PDF content into memory — no disk I/O."""

    def __init__(
        self,
        use_scihub: bool = False,
        timeout: int = 30,
        concurrency: int = 4,
    ) -> None:
        self.use_scihub = use_scihub
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=float(timeout), write=10.0, pool=5.0),
            headers={"User-Agent": "AcademicPipeline/0.1 (+https://github.com/zspec-oos/wisp)"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, result: SearchResult) -> bytes | None:
        """Return raw PDF bytes for *result*, or None if unavailable."""
        async with self._sem:
            return await self._fetch_one(result)

    async def _fetch_one(self, result: SearchResult) -> bytes | None:
        # 1. Legal open-access URL (Unpaywall / provider)
        if result.oa_pdf_url:
            data = await _fetch_direct(self._client, result.oa_pdf_url)
            if data:
                _logger.info("oa_fetch_ok title=%r bytes=%d", result.title[:50], len(data))
                return data

        # 2. Sci-Hub (opt-in only)
        if self.use_scihub and result.doi:
            _logger.info("trying_scihub doi=%s", result.doi)
            data = await _scihub_fetch(self._client, result.doi)
            if data:
                _logger.info("scihub_fetch_ok doi=%s bytes=%d", result.doi, len(data))
                return data

        _logger.warning("content_unavailable title=%r doi=%s", result.title[:50], result.doi)
        return None
