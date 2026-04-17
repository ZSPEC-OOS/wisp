"""CLI entry point: academic-pipeline."""
from __future__ import annotations

import argparse
import asyncio
import json
import os


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="academic-pipeline",
        description="Search academic papers and answer questions from their content.",
    )
    p.add_argument("--prompt", required=True, help="Natural language research prompt")
    p.add_argument("--question", default="", help="Follow-up question to answer from each paper")
    p.add_argument("--max-papers", type=int, default=3, help="Max papers to retrieve (default: 3)")
    p.add_argument(
        "--use-scihub",
        type=lambda v: v.lower() not in ("0", "false", "no"),
        default=False,
        metavar="true|false",
        help="Enable Sci-Hub fallback for paywalled content (default: false)",
    )
    p.add_argument("--output-format", choices=["text", "json"], default="text")
    return p


async def _run(args: argparse.Namespace) -> None:
    from packages.academic_pipeline.pipeline import AcademicPipeline, PipelineConfig

    cfg = PipelineConfig(
        prompt=args.prompt,
        question=args.question,
        max_papers=args.max_papers,
        use_scihub=args.use_scihub,
        mailto=os.environ.get("WISP_ACADEMIC_MAILTO", ""),
        s2_api_key=os.environ.get("WISP_S2_API_KEY", ""),
        llm_base_url=os.environ.get("WISP_LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("WISP_LLM_API_KEY", ""),
        llm_model=os.environ.get("WISP_LLM_MODEL", ""),
    )

    results = await AcademicPipeline(cfg).run()

    if args.output_format == "json":
        print(json.dumps([
            {
                "title": r.title,
                "doi": r.doi,
                "authors": r.authors,
                "year": r.publication_year,
                "url": r.url,
                "content_fetched": r.content_fetched,
                "answer": r.answer,
                "parse_error": r.parse_error,
            }
            for r in results
        ], indent=2))
        return

    if not results:
        print("No papers found for the given prompt.")
        return

    for i, r in enumerate(results, 1):
        doi_str = f"  DOI: {r.doi}" if r.doi else ""
        print(f"\nPaper {i}: \"{r.title}\"{doi_str}")
        if r.authors:
            suffix = " et al." if len(r.authors) > 3 else ""
            print(f"  Authors: {', '.join(r.authors[:3])}{suffix}")
        if r.publication_year:
            print(f"  Year: {r.publication_year}")
        status = "content retrieved" if r.content_fetched else "content unavailable"
        print(f"  → {status}")
        if r.answer:
            print(f"  → Answer: {r.answer}")
        elif args.question and r.parse_error:
            print(f"  → Parse error: {r.parse_error}")
        elif args.question and not r.content_fetched:
            print("  → Cannot answer: no accessible full text found")


def main() -> None:
    asyncio.run(_run(_build_parser().parse_args()))


if __name__ == "__main__":
    main()
