"""Integration tests for the academic paper pipeline."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.academic_pipeline.answer import _bm25_retrieve, _chunk_text, answer_question
from packages.academic_pipeline.download import (
    PdfFetcher,
    _extract_pdf_url_from_html,
    _is_pdf,
)
from packages.academic_pipeline.parse import parse_bytes
from packages.academic_pipeline.pipeline import AcademicPipeline, PipelineConfig
from packages.academic_pipeline.search import AcademicSearcher
from packages.common.models import SearchResult


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_result(**kwargs) -> SearchResult:
    defaults = dict(
        title="Test Paper",
        url="https://arxiv.org/abs/1234.5678",
        snippet="Abstract text.",
        source_domain="arxiv.org",
        rank=1,
        provider="arxiv",
        retrieved_at=datetime.now(timezone.utc),
        doi="10.1234/test",
        oa_pdf_url="https://arxiv.org/pdf/1234.5678",
    )
    defaults.update(kwargs)
    return SearchResult(**defaults)


# ── Unit: chunking and BM25 ──────────────────────────────────────────────────

def test_chunk_text_produces_multiple_chunks():
    text = " ".join(["word"] * 350)
    chunks = _chunk_text(text, size=300, overlap=50)
    assert len(chunks) >= 2
    assert all(len(c.split()) <= 300 for c in chunks)


def test_chunk_text_short_input():
    assert _chunk_text("short text") == ["short text"]


def test_bm25_ranks_relevant_chunk_first():
    chunks = [
        "Neural networks are trained by gradient descent.",
        "The weather in Paris is often rainy in autumn.",
        "Backpropagation is the algorithm used to train neural networks.",
    ]
    top = _bm25_retrieve("how are neural networks trained?", chunks, top_k=2)
    assert "weather" not in top[0]


# ── Unit: PDF detection and Sci-Hub HTML parsing ──────────────────────────────

def test_is_pdf_positive():
    assert _is_pdf(b"%PDF-1.4 rest of content")


def test_is_pdf_negative():
    assert not _is_pdf(b"<html>not a pdf</html>")


def test_extract_pdf_url_from_embed():
    html = '<html><body><embed type="application/pdf" src="//cdn.sci-hub.se/abc.pdf"/></body></html>'
    url = _extract_pdf_url_from_html(html, "https://sci-hub.se")
    assert url == "https://cdn.sci-hub.se/abc.pdf"


def test_extract_pdf_url_from_iframe():
    html = '<html><body><iframe id="pdf" src="//cdn.sci-hub.se/xyz.pdf"></iframe></body></html>'
    url = _extract_pdf_url_from_html(html, "https://sci-hub.se")
    assert url == "https://cdn.sci-hub.se/xyz.pdf"


def test_extract_pdf_url_relative_path():
    html = '<html><body><embed src="/downloads/paper.pdf"/></body></html>'
    url = _extract_pdf_url_from_html(html, "https://sci-hub.se")
    assert url == "https://sci-hub.se/downloads/paper.pdf"


def test_extract_pdf_url_not_found():
    assert _extract_pdf_url_from_html("<html><body>nothing</body></html>", "https://sci-hub.se") is None


# ── Integration: AcademicSearcher ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_academic_searcher_deduplicates_by_doi():
    r1 = _make_result(doi="10.1234/dup", url="https://arxiv.org/abs/1")
    r2 = _make_result(doi="10.1234/dup", url="https://arxiv.org/abs/2")
    r3 = _make_result(doi="10.9999/other", url="https://arxiv.org/abs/3")

    searcher = AcademicSearcher()
    with (
        patch.object(searcher._providers[0], "search", new=AsyncMock(return_value=[r1])),
        patch.object(searcher._providers[1], "search", new=AsyncMock(return_value=[r2])),
        patch.object(searcher._providers[2], "search", new=AsyncMock(return_value=[r3])),
        patch.object(searcher._crossref, "enrich", new=AsyncMock(side_effect=lambda r: r)),
        patch.object(searcher._unpaywall, "enrich", new=AsyncMock(side_effect=lambda r: r)),
    ):
        results = await searcher.search("test query", max_papers=5)

    assert len(results) == 2
    assert sum(1 for r in results if r.doi == "10.1234/dup") == 1


@pytest.mark.asyncio
async def test_academic_searcher_handles_provider_exception():
    r1 = _make_result(doi="10.1234/good")
    searcher = AcademicSearcher()
    with (
        patch.object(searcher._providers[0], "search", new=AsyncMock(side_effect=RuntimeError("down"))),
        patch.object(searcher._providers[1], "search", new=AsyncMock(return_value=[r1])),
        patch.object(searcher._providers[2], "search", new=AsyncMock(return_value=[])),
        patch.object(searcher._crossref, "enrich", new=AsyncMock(side_effect=lambda r: r)),
        patch.object(searcher._unpaywall, "enrich", new=AsyncMock(side_effect=lambda r: r)),
    ):
        results = await searcher.search("test query", max_papers=5)

    assert len(results) == 1


# ── Integration: PdfFetcher ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetcher_returns_pdf_bytes():
    fake_pdf = b"%PDF-1.4 fake"
    result = _make_result(oa_pdf_url="https://example.com/paper.pdf")

    import httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.content = fake_pdf
    mock_resp.headers = {"content-type": "application/pdf"}
    mock_resp.raise_for_status = MagicMock()

    fetcher = PdfFetcher()
    with patch.object(fetcher._client, "get", new=AsyncMock(return_value=mock_resp)):
        data = await fetcher.fetch(result)

    assert data == fake_pdf
    await fetcher.aclose()


@pytest.mark.asyncio
async def test_fetcher_returns_none_on_failure():
    import httpx
    result = _make_result(oa_pdf_url="https://example.com/paper.pdf", doi=None)

    fetcher = PdfFetcher(use_scihub=False)
    with patch.object(fetcher._client, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
        data = await fetcher.fetch(result)

    assert data is None
    await fetcher.aclose()


@pytest.mark.asyncio
async def test_fetcher_scihub_parses_html_landing_page():
    """Fetcher should parse Sci-Hub HTML page and retrieve embedded PDF."""
    landing_html = b'<html><embed src="//cdn.sci-hub.se/paper.pdf"/></html>'
    fake_pdf = b"%PDF-1.4 real"

    result = _make_result(oa_pdf_url=None, doi="10.1234/paywalled")
    fetcher = PdfFetcher(use_scihub=True)

    call_count = 0

    async def _mock_get(url, **kw):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "cdn.sci-hub" in url:
            resp.content = fake_pdf
        else:
            resp.content = landing_html
            resp.text = landing_html.decode()
        return resp

    with patch.object(fetcher._client, "get", new=AsyncMock(side_effect=_mock_get)):
        data = await fetcher.fetch(result)

    assert data == fake_pdf
    await fetcher.aclose()


# ── Integration: answer_question ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_answer_extractive_fallback():
    text = "The main contribution of this paper is a novel attention mechanism. " * 20
    answer = await answer_question(
        "What is the main contribution?",
        text,
        llm_base_url="",
        llm_model="",
    )
    assert len(answer) > 10


@pytest.mark.asyncio
async def test_answer_llm_path():
    text = "Dataset: ZINC, ChEMBL, Tox21 were used for evaluation. " * 30

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "ZINC, ChEMBL, and Tox21."}}]
    }

    with patch("packages.academic_pipeline.answer.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        answer = await answer_question(
            "What datasets were used?",
            text,
            llm_base_url="http://localhost:8001/v1",
            llm_api_key="key",
            llm_model="test-model",
        )

    assert "ZINC" in answer


# ── Integration: full pipeline ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_end_to_end():
    paper = _make_result(
        title="GNNs for Drug Discovery",
        doi="10.9999/gnn",
        oa_pdf_url="https://example.com/gnn.pdf",
    )
    fake_bytes = b"%PDF-1.4 The datasets used were ZINC and ChEMBL."

    cfg = PipelineConfig(
        prompt="graph neural networks drug discovery",
        question="What datasets were used?",
        max_papers=1,
    )
    pipeline = AcademicPipeline(cfg)

    with (
        patch.object(pipeline._searcher, "search", new=AsyncMock(return_value=[paper])),
        patch.object(pipeline._fetcher, "fetch", new=AsyncMock(return_value=fake_bytes)),
        patch("packages.academic_pipeline.pipeline.parse_bytes", return_value="ZINC and ChEMBL data."),
        patch(
            "packages.academic_pipeline.pipeline.answer_question",
            new=AsyncMock(return_value="ZINC and ChEMBL were used."),
        ),
    ):
        results = await pipeline.run()

    assert len(results) == 1
    r = results[0]
    assert r.title == "GNNs for Drug Discovery"
    assert r.content_fetched is True
    assert r.answer == "ZINC and ChEMBL were used."
    assert r.parse_error is None


@pytest.mark.asyncio
async def test_pipeline_graceful_when_content_unavailable():
    paper = _make_result(oa_pdf_url=None, doi=None)

    cfg = PipelineConfig(
        prompt="test query",
        question="What is the method?",
        max_papers=1,
    )
    pipeline = AcademicPipeline(cfg)

    with (
        patch.object(pipeline._searcher, "search", new=AsyncMock(return_value=[paper])),
        patch.object(pipeline._fetcher, "fetch", new=AsyncMock(return_value=None)),
    ):
        results = await pipeline.run()

    r = results[0]
    assert r.content_fetched is False
    assert r.answer is None
    assert r.parse_error is None
