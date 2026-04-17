from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

from packages.common.url import validate_safe_url


class SearchRequest(BaseModel):
    query: str = Field(min_length=2)
    topic: Literal["general", "news", "finance", "academic"] = "general"
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
    js_render: bool = False

    @field_validator("urls", mode="before")
    @classmethod
    def urls_must_be_safe(cls, v: list) -> list:
        for url in v:
            validate_safe_url(str(url))
        return v


class CrawlRequest(BaseModel):
    seed_url: HttpUrl
    instructions: str | None = None
    max_pages: int = Field(default=20, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=6)
    concurrency: int = Field(default=5, ge=1, le=20)
    allowed_domains: list[str] | None = None
    timeout_seconds: int = Field(default=10, ge=2, le=60)

    @field_validator("seed_url", mode="before")
    @classmethod
    def seed_url_must_be_safe(cls, v) -> str:
        validate_safe_url(str(v))
        return v


class MapRequest(BaseModel):
    seed_url: HttpUrl
    max_pages: int = Field(default=20, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=6)

    @field_validator("seed_url", mode="before")
    @classmethod
    def seed_url_must_be_safe(cls, v) -> str:
        validate_safe_url(str(v))
        return v


class ResearchRequest(BaseModel):
    query: str
    mode: Literal["concise", "report", "structured"] = "concise"
    max_search_rounds: int = Field(default=2, ge=1, le=6)
    max_sources: int = Field(default=6, ge=1, le=20)
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    # never  → always use native extractive path
    # auto   → gating policy decides based on evidence profile
    # always → call LLM if enabled and available, fall back to native on failure
    synthesis_mode: Literal["never", "auto", "always"] = "auto"


class AcademicRequest(BaseModel):
    prompt: str = Field(min_length=2, description="Natural language research prompt")
    question: str = Field(default="", description="Follow-up question to answer from each paper")
    max_papers: int = Field(default=3, ge=1, le=10)
    use_scihub: bool = Field(default=False, description="Enable Sci-Hub fallback (must be globally enabled in config)")
