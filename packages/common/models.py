from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Citation(BaseModel):
    url: HttpUrl
    title: str | None = None
    snippet: str
    passage: str | None = None


class SearchResult(BaseModel):
    title: str
    url: HttpUrl
    snippet: str
    source_domain: str
    rank: int
    provider: str
    retrieved_at: datetime
    trust_score: float = 0.5
    freshness_score: float = 0.5


class Passage(BaseModel):
    text: str
    score: float = 0.0
    source_url: HttpUrl


class ExtractedDocument(BaseModel):
    url: HttpUrl
    canonical_url: HttpUrl | None = None
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    status: Literal["ok", "error"]
    format: Literal["markdown", "text"]
    content: str = ""
    passages: list[Passage] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)
