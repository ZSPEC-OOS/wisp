"""Stage 2: Download PDFs from open-access sources.

Resolution order:
  1. result.oa_pdf_url  (already resolved by Unpaywall / provider)
  2. Sci-Hub mirror list (optional, user must opt-in via use_scihub=True)

PDFs are saved to output_dir/<sanitised-title>.pdf.  Returns the local path
on success, or None if no source could deliver a valid PDF.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

import httpx

from packages.common.models import SearchResult

_logger = logging.getLogger("wisp.academic_pipeline.download")

# Known Sci-Hub mirror pattern — callers opt-in; we never hit these by default.
_SCIHUB_MIRRORS = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
]

_PDF_MAGIC = b"%PDF"


def _safe_filename(title: str, doi: str | None) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")[:60]
    suffix = hashlib.md5((doi or title).encode()).hexdigest()[:8]
    return f"{slug}_{suffix}.pdf"


def _is_pdf(data: bytes) -> bool:
    return data[:4] == _PDF_MAGIC


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
        if _is_pdf(r.content):
            return r.content
        _logger.debug("not_a_pdf url=%s content_type=%s", url, r.headers.get("content-type"))
    except Exception as exc:
        _logger.debug("fetch_failed url=%s error=%s", url, exc)
    return None


async def _scihub_fetch(client: httpx.AsyncClient, doi: str) -> bytes | None:
    for mirror in _SCIHUB_MIRRORS:
        data = await _fetch(client, f"{mirror}/{doi}")
        if data:
            return data
    return None


class PdfDownloader:
    def __init__(
        self,
        output_dir: str = "./papers",
        use_scihub: bool = False,
        timeout: int = 30,
        concurrency: int = 4,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.use_scihub = use_scihub
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=float(timeout), write=10.0, pool=5.0),
            headers={"User-Agent": "AcademicPipeline/0.1 (open-access only)"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def download(self, result: SearchResult) -> str | None:
        """Download PDF for *result*; return local path or None."""
        async with self._sem:
            return await self._download_one(result)

    async def _download_one(self, result: SearchResult) -> str | None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(result.title, result.doi)
        dest = self.output_dir / filename

        if dest.exists():
            _logger.info("already_cached path=%s", dest)
            return str(dest)

        data: bytes | None = None

        # 1. Direct OA PDF URL
        if result.oa_pdf_url:
            data = await _fetch(self._client, result.oa_pdf_url)

        # 2. Sci-Hub (opt-in only)
        if data is None and self.use_scihub and result.doi:
            _logger.info("trying_scihub doi=%s", result.doi)
            data = await _scihub_fetch(self._client, result.doi)

        if data is None:
            _logger.warning("pdf_unavailable title=%r doi=%s", result.title[:60], result.doi)
            return None

        dest.write_bytes(data)
        _logger.info("pdf_saved path=%s bytes=%d", dest, len(data))
        return str(dest)
