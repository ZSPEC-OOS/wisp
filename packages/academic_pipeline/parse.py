"""Stage 3: Extract text from a PDF file.

Tries pdfplumber first (richer layout awareness), falls back to pypdf, then
pymupdf (fitz).  All three are optional; at least one should be installed via
the `academic` or `academic-pipeline` extra.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger("wisp.academic_pipeline.parse")

try:
    import pdfplumber as _pdfplumber  # pip install pdfplumber
    _PDFPLUMBER = True
except ImportError:
    _PDFPLUMBER = False

try:
    import pypdf as _pypdf  # already in the `academic` extra
    _PYPDF = True
except ImportError:
    _PYPDF = False

try:
    import fitz as _fitz  # pip install pymupdf
    _FITZ = True
except ImportError:
    _FITZ = False


def _extract_pdfplumber(path: str) -> str:
    parts: list[str] = []
    with _pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text and text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts)


def _extract_pypdf(path: str) -> str:
    import io
    reader = _pypdf.PdfReader(path)
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _extract_fitz(path: str) -> str:
    doc = _fitz.open(path)
    parts: list[str] = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            parts.append(text.strip())
    doc.close()
    return "\n\n".join(parts)


def extract_text(pdf_path: str) -> str:
    """Return extracted text from *pdf_path*, using the best available library."""
    if _PDFPLUMBER:
        try:
            text = _extract_pdfplumber(pdf_path)
            if text.strip():
                return text
        except Exception as exc:
            _logger.warning("pdfplumber_failed path=%s error=%s", pdf_path, exc)

    if _PYPDF:
        try:
            text = _extract_pypdf(pdf_path)
            if text.strip():
                return text
        except Exception as exc:
            _logger.warning("pypdf_failed path=%s error=%s", pdf_path, exc)

    if _FITZ:
        try:
            text = _extract_fitz(pdf_path)
            if text.strip():
                return text
        except Exception as exc:
            _logger.warning("fitz_failed path=%s error=%s", pdf_path, exc)

    raise RuntimeError(
        f"No PDF parser available or all parsers failed for {pdf_path}. "
        "Install at least one of: pdfplumber, pypdf, pymupdf."
    )
