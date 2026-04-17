"""Main orchestrator: prompt → papers → PDF bytes → text → answers.

Nothing is written to disk.  PDF content is fetched into memory, parsed,
and discarded after the answer is produced.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from packages.academic_pipeline.answer import answer_question
from packages.academic_pipeline.download import PdfFetcher
from packages.academic_pipeline.parse import parse_bytes
from packages.academic_pipeline.search import AcademicSearcher
from packages.common.models import SearchResult

_logger = logging.getLogger("wisp.academic_pipeline")


@dataclass
class PaperResult:
    title: str
    doi: str | None
    authors: list[str]
    publication_year: int | None
    url: str
    oa_pdf_url: str | None
    content_fetched: bool          # True if PDF bytes were retrieved
    parse_error: str | None        # Set if text extraction failed
    answer: str | None             # Set when question provided and content available
    provider: str


@dataclass
class PipelineConfig:
    prompt: str
    question: str = ""
    max_papers: int = 5
    use_scihub: bool = False
    # Search settings
    mailto: str = ""
    s2_api_key: str = ""
    # LLM settings (optional — falls back to extractive answer)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 30.0


class AcademicPipeline:
    def __init__(self, config: PipelineConfig) -> None:
        self._cfg = config
        self._searcher = AcademicSearcher(
            mailto=config.mailto,
            s2_api_key=config.s2_api_key,
        )
        self._fetcher = PdfFetcher(use_scihub=config.use_scihub)

    async def run(self) -> list[PaperResult]:
        cfg = self._cfg
        _logger.info("pipeline_start prompt=%r max_papers=%d", cfg.prompt, cfg.max_papers)

        papers = await self._searcher.search(cfg.prompt, max_papers=cfg.max_papers)
        _logger.info("found %d papers", len(papers))

        # Fetch PDF bytes concurrently for all papers
        pdf_data: list[bytes | None] = await asyncio.gather(
            *[self._fetcher.fetch(p) for p in papers]
        )

        results = await asyncio.gather(
            *[self._process(paper, data) for paper, data in zip(papers, pdf_data)]
        )

        await self._fetcher.aclose()
        return list(results)

    async def _process(self, paper: SearchResult, data: bytes | None) -> PaperResult:
        cfg = self._cfg
        parse_error: str | None = None
        answer: str | None = None

        if data and cfg.question:
            try:
                text = await asyncio.to_thread(parse_bytes, data)
                answer = await answer_question(
                    cfg.question,
                    text,
                    title=paper.title,
                    doi=paper.doi,
                    llm_base_url=cfg.llm_base_url,
                    llm_api_key=cfg.llm_api_key,
                    llm_model=cfg.llm_model,
                    llm_timeout=cfg.llm_timeout,
                )
            except Exception as exc:
                parse_error = str(exc)
                _logger.warning("process_failed title=%r err=%s", paper.title[:60], exc)

        return PaperResult(
            title=paper.title,
            doi=paper.doi,
            authors=paper.authors,
            publication_year=paper.publication_year,
            url=str(paper.url),
            oa_pdf_url=paper.oa_pdf_url,
            content_fetched=data is not None,
            parse_error=parse_error,
            answer=answer,
            provider=paper.provider,
        )
