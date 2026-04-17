from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Query(Base):
    __tablename__ = "queries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SearchResultRow(Base):
    __tablename__ = "search_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    url: Mapped[str] = mapped_column(String(1500), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class FetchedPage(Base):
    __tablename__ = "fetched_pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1500), unique=True)
    status: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ExtractedDocumentRow(Base):
    __tablename__ = "extracted_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1500), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed_url: Mapped[str] = mapped_column(String(1500))
    status: Mapped[str] = mapped_column(String(32), default="pending")


class CrawlNode(Base):
    __tablename__ = "crawl_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    url: Mapped[str] = mapped_column(String(1500), index=True)


class CrawlEdge(Base):
    __tablename__ = "crawl_edges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    source_url: Mapped[str] = mapped_column(String(1500))
    target_url: Mapped[str] = mapped_column(String(1500))


class ResearchTask(Base):
    __tablename__ = "research_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(1000))
    mode: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict] = mapped_column(JSON)


class CitationRow(Base):
    __tablename__ = "citations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    url: Mapped[str] = mapped_column(String(1500))
    snippet: Mapped[str] = mapped_column(Text)


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
