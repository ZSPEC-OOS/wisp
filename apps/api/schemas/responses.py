from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class APIError(BaseModel):
    error: str
    detail: str


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    cache_size: int = 0
    cache_max_size: int = 0
    checks: dict | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[dict]
    quick_answer: str | None = None
    citations: list[dict]
    extracted: list[dict] | None = None
    warnings: list[str] | None = None


class ExtractResponse(BaseModel):
    documents: list[dict]
    success_count: int = 0
    failure_count: int = 0


class CrawlResponse(BaseModel):
    pages_crawled: int
    nodes: list[dict]
    edges: list[dict]
    discovered_urls: list[str]
    failures: list[dict]


class MapResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    clusters: dict
    site_summary: dict


class ResearchResponse(BaseModel):
    final_answer: str
    executive_summary: str
    detailed_report: str
    structured_answer: dict | None = None
    sources: list[dict]
    citation_spans: list[dict]
    uncertainty_notes: str
    confidence_score: float = 0.0
    research_trace: dict
    mode: str


class CrawlJobResponse(BaseModel):
    job_id: str
    status: Literal["pending", "running", "done", "failed"]
    created_at: str
    result: dict | None = None
    error: str | None = None
