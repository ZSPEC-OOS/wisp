"""Main orchestrator: prompt → papers → PDFs → text → answers."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from packages.academic_pipeline.answer import answer_question
from packages.academic_pipeline.download import PdfDownloader
from packages.academic_pipeline.parse import extract_text
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
    pdf_path: str | None           # None if download failed
    parse_error: str | None        # None if parsing succeeded
    answer: str | None             # None if question was not provided
    provider: str


@dataclass
class PipelineConfig:
    prompt: str
    question: str = ""
    max_papers: int = 5
    use_scihub: bool = False
    output_dir: str = "./papers"
    # Search settings
    mailto: str = ""
    s2_api_key: str = ""
    # LLM settings (optional)
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
        self._downloader = PdfDownloader(
            output_dir=config.output_dir,
            use_scihub=config.use_scihub,
        )

    async def run(self) -> list[PaperResult]:
        cfg = self._cfg
        _logger.info("pipeline_start prompt=%r max_papers=%d", cfg.prompt, cfg.max_papers)

        papers = await self._searcher.search(cfg.prompt, max_papers=cfg.max_papers)
        _logger.info("found %d papers", len(papers))

        # Download PDFs concurrently
        pdf_paths: list[str | None] = await asyncio.gather(
            *[self._downloader.download(p) for p in papers], return_exceptions=False
        )

        results: list[PaperResult] = []
        for paper, pdf_path in zip(papers, pdf_paths):
            results.append(await self._process(paper, pdf_path))

        await self._downloader.aclose()
        return results

    async def _process(self, paper: SearchResult, pdf_path: str | None) -> PaperResult:
        cfg = self._cfg
        parse_error: str | None = None
        answer: str | None = None

        if pdf_path and cfg.question:
            try:
                text = await asyncio.to_thread(extract_text, pdf_path)
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
                _logger.warning("process_failed title=%r error=%s", paper.title[:60], exc)

        return PaperResult(
            title=paper.title,
            doi=paper.doi,
            authors=paper.authors,
            publication_year=paper.publication_year,
            url=str(paper.url),
            oa_pdf_url=paper.oa_pdf_url,
            pdf_path=pdf_path,
            parse_error=parse_error,
            answer=answer,
            provider=paper.provider,
        )
