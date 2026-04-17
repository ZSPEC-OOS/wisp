"""Integration tests for the academic paper pipeline.

These tests use mocks to avoid hitting real APIs or downloading real PDFs.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.academic_pipeline.answer import answer_question, _chunk_text, _bm25_retrieve
from packages.academic_pipeline.download import PdfDownloader, _is_pdf, _safe_filename
from packages.academic_pipeline.parse import extract_text
from packages.academic_pipeline.pipeline import AcademicPipeline, PipelineConfig
from packages.academic_pipeline.search import AcademicSearcher
from packages.common.models import SearchResult


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_result(**kwargs) -> SearchResult:
    from datetime import datetime, timezone
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


# ── Unit: chunking and BM25 retrieval ────────────────────────────────────────

def test_chunk_text_basic():
    words = ["word"] * 350
    text = " ".join(words)
    chunks = _chunk_text(text, size=300, overlap=50)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk.split()) <= 300


def test_chunk_text_short():
    chunks = _chunk_text("short text", size=300, overlap=50)
    assert chunks == ["short text"]


def test_bm25_retrieve_ranks_relevant_chunk():
    chunks = [
        "Neural networks are trained by gradient descent.",
        "The weather in Paris is often rainy.",
        "Backpropagation is the algorithm used to train neural networks.",
    ]
    top = _bm25_retrieve("how are neural networks trained?", chunks, top_k=2)
    # Both neural-network chunks should outrank the weather chunk
    assert "The weather in Paris" not in top[0]


# ── Unit: PDF helpers ─────────────────────────────────────────────────────────

def test_is_pdf_positive():
    assert _is_pdf(b"%PDF-1.4 rest of content")


def test_is_pdf_negative():
    assert not _is_pdf(b"<html>not a pdf</html>")


def test_safe_filename_no_special_chars():
    name = _safe_filename("GNNs for Drug Discovery!", "10.1234/abc")
    assert name.endswith(".pdf")
    assert "/" not in name
    assert "!" not in name


# ── Integration: AcademicSearcher ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_academic_searcher_deduplicates_by_doi():
    r1 = _make_result(doi="10.1234/dup", url="https://arxiv.org/abs/1")
    r2 = _make_result(doi="10.1234/dup", url="https://arxiv.org/abs/2")  # same DOI
    r3 = _make_result(doi="10.9999/other", url="https://arxiv.org/abs/3")

    searcher = AcademicSearcher()
    # Patch all three providers to return controlled results
    with (
        patch.object(searcher._providers[0], "search", new=AsyncMock(return_value=[r1])),
        patch.object(searcher._providers[1], "search", new=AsyncMock(return_value=[r2])),
        patch.object(searcher._providers[2], "search", new=AsyncMock(return_value=[r3])),
        patch.object(searcher._crossref, "enrich", new=AsyncMock(side_effect=lambda r: r)),
        patch.object(searcher._unpaywall, "enrich", new=AsyncMock(side_effect=lambda r: r)),
    ):
        results = await searcher.search("test query", max_papers=5)

    dois = [r.doi for r in results]
    assert dois.count("10.1234/dup") == 1
    assert len(results) == 2


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


# ── Integration: PdfDownloader ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_downloader_saves_valid_pdf(tmp_path):
    fake_pdf = b"%PDF-1.4 fake content"
    result = _make_result(oa_pdf_url="https://example.com/paper.pdf")

    import httpx
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = fake_pdf
    mock_response.headers = {"content-type": "application/pdf"}
    mock_response.raise_for_status = MagicMock()

    downloader = PdfDownloader(output_dir=str(tmp_path))
    with patch.object(downloader._client, "get", new=AsyncMock(return_value=mock_response)):
        path = await downloader.download(result)

    assert path is not None
    assert path.endswith(".pdf")
    assert (tmp_path / os.path.basename(path)).read_bytes() == fake_pdf
    await downloader.aclose()


@pytest.mark.asyncio
async def test_downloader_returns_none_on_failure(tmp_path):
    import httpx
    result = _make_result(oa_pdf_url="https://example.com/paper.pdf", doi=None)

    downloader = PdfDownloader(output_dir=str(tmp_path), use_scihub=False)
    with patch.object(downloader._client, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
        path = await downloader.download(result)

    assert path is None
    await downloader.aclose()


@pytest.mark.asyncio
async def test_downloader_uses_cache(tmp_path):
    fake_pdf = b"%PDF-1.4 cached"
    result = _make_result(oa_pdf_url="https://example.com/paper.pdf")

    downloader = PdfDownloader(output_dir=str(tmp_path))
    # Pre-create the file that would be saved
    expected_name = _safe_filename(result.title, result.doi)
    (tmp_path / expected_name).write_bytes(fake_pdf)

    with patch.object(downloader._client, "get", new=AsyncMock(side_effect=AssertionError("should not fetch"))):
        path = await downloader.download(result)

    assert path is not None
    await downloader.aclose()


# ── Integration: answer_question ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_answer_question_no_llm_returns_top_chunk():
    text = "The main contribution of this paper is a novel attention mechanism. " * 20
    answer = await answer_question(
        "What is the main contribution?",
        text,
        llm_base_url="",
        llm_model="",
    )
    assert "attention" in answer.lower() or len(answer) > 10


@pytest.mark.asyncio
async def test_answer_question_llm_path(monkeypatch):
    text = "Dataset: ZINC, ChEMBL, Tox21 were used for evaluation. " * 30

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "ZINC, ChEMBL, and Tox21."}}]
    }

    import httpx
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
async def test_pipeline_end_to_end(tmp_path):
    paper = _make_result(
        title="GNNs for Drug Discovery",
        doi="10.9999/gnn",
        oa_pdf_url="https://example.com/gnn.pdf",
    )
    fake_pdf = b"%PDF-1.4 The datasets used were ZINC and ChEMBL. " + b"x" * 100
    fake_text = "The datasets used were ZINC and ChEMBL."

    cfg = PipelineConfig(
        prompt="graph neural networks drug discovery",
        question="What datasets were used?",
        max_papers=1,
        output_dir=str(tmp_path),
    )
    pipeline = AcademicPipeline(cfg)

    with (
        patch.object(pipeline._searcher, "search", new=AsyncMock(return_value=[paper])),
        patch.object(pipeline._downloader, "download", new=AsyncMock(return_value=str(tmp_path / "paper.pdf"))),
        patch("packages.academic_pipeline.pipeline.extract_text", return_value=fake_text),
        patch(
            "packages.academic_pipeline.pipeline.answer_question",
            new=AsyncMock(return_value="ZINC and ChEMBL were used."),
        ),
    ):
        results = await pipeline.run()

    assert len(results) == 1
    r = results[0]
    assert r.title == "GNNs for Drug Discovery"
    assert r.doi == "10.9999/gnn"
    assert r.pdf_path is not None
    assert r.answer == "ZINC and ChEMBL were used."
    assert r.parse_error is None


@pytest.mark.asyncio
async def test_pipeline_graceful_on_no_pdf(tmp_path):
    paper = _make_result(oa_pdf_url=None, doi=None)

    cfg = PipelineConfig(
        prompt="test query",
        question="What is the method?",
        max_papers=1,
        output_dir=str(tmp_path),
    )
    pipeline = AcademicPipeline(cfg)

    with (
        patch.object(pipeline._searcher, "search", new=AsyncMock(return_value=[paper])),
        patch.object(pipeline._downloader, "download", new=AsyncMock(return_value=None)),
    ):
        results = await pipeline.run()

    assert results[0].pdf_path is None
    assert results[0].answer is None
    assert results[0].parse_error is None
