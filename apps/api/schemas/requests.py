from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class SearchRequest(BaseModel):
    query: str = Field(min_length=2)
    topic: Literal["general", "news", "finance"] = "general"
    max_results: int = Field(default=8, ge=1, le=20)
    include_answer: bool = True
    include_raw_content: bool = False
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None


class ExtractRequest(BaseModel):
    urls: list[HttpUrl]
    format: Literal["markdown", "text"] = "markdown"
    include_images: bool = False
    timeout_seconds: int = Field(default=12, ge=2, le=90)


class CrawlRequest(BaseModel):
    seed_url: HttpUrl
    instructions: str | None = None
    max_pages: int = Field(default=20, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=6)
    allowed_domains: list[str] | None = None
    timeout_seconds: int = Field(default=10, ge=2, le=60)


class MapRequest(BaseModel):
    seed_url: HttpUrl
    max_pages: int = Field(default=20, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=6)


class ResearchRequest(BaseModel):
    query: str
    mode: Literal["concise", "report", "structured"] = "concise"
    max_search_rounds: int = Field(default=2, ge=1, le=6)
    max_sources: int = Field(default=6, ge=1, le=20)
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
