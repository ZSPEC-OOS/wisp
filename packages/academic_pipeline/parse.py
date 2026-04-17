"""Stage 3: Extract text from PDF data.

Two entry points:
  parse_bytes(data: bytes) -> str   — for in-memory PDF bytes (primary path)
  extract_text(path: str)  -> str   — for on-disk files (kept for CLI/testing)

Tries pdfplumber first (richer layout awareness), falls back to pypdf, then
pymupdf (fitz).  All three are optional; at least one should be installed via
the `academic` or `academic-pipeline` extra.
"""
from __future__ import annotations

import io
import logging

_logger = logging.getLogger("wisp.academic_pipeline.parse")

try:
    import pdfplumber as _pdfplumber
    _PDFPLUMBER = True
except ImportError:
    _PDFPLUMBER = False

try:
    import pypdf as _pypdf
    _PYPDF = True
except ImportError:
    _PYPDF = False

try:
    import fitz as _fitz
    _FITZ = True
except ImportError:
    _FITZ = False


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p.strip())


def _parse_pdfplumber_bytes(data: bytes) -> str:
    parts: list[str] = []
    with _pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                parts.append(text.strip())
    return _join(parts)


def _parse_pypdf_bytes(data: bytes) -> str:
    reader = _pypdf.PdfReader(io.BytesIO(data))
    return _join([page.extract_text() or "" for page in reader.pages])


def _parse_fitz_bytes(data: bytes) -> str:
    doc = _fitz.open(stream=data, filetype="pdf")
    parts = [page.get_text() for page in doc]
    doc.close()
    return _join(parts)


def parse_bytes(data: bytes) -> str:
    """Extract text from raw PDF *data* (in-memory)."""
    for name, available, fn in [
        ("pdfplumber", _PDFPLUMBER, _parse_pdfplumber_bytes),
        ("pypdf",      _PYPDF,      _parse_pypdf_bytes),
        ("pymupdf",    _FITZ,       _parse_fitz_bytes),
    ]:
        if not available:
            continue
        try:
            text = fn(data)
            if text.strip():
                return text
        except Exception as exc:
            _logger.warning("%s_failed err=%s", name, exc)

    raise RuntimeError(
        "No PDF parser succeeded. Install at least one of: pdfplumber, pypdf, pymupdf."
    )


def extract_text(pdf_path: str) -> str:
    """Extract text from a PDF file at *pdf_path*."""
    with open(pdf_path, "rb") as f:
        return parse_bytes(f.read())
