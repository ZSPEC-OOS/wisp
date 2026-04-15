from __future__ import annotations

from pydantic import BaseModel


class APIError(BaseModel):
    error: str
    detail: str


class HealthResponse(BaseModel):
    status: str


class SearchResponse(BaseModel):
    query: str
    results: list[dict]
    quick_answer: str | None = None
    citations: list[dict]
    extracted: list[dict] | None = None


class ExtractResponse(BaseModel):
    documents: list[dict]


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
    sources: list[dict]
    citation_spans: list[dict]
    uncertainty_notes: str
    research_trace: dict
    mode: str
