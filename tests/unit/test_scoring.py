from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.common.models import SearchResult
from packages.search.pipeline import _citation_boost, _rank_score, score_result


def _result(**kwargs) -> SearchResult:
    defaults = dict(
        title="Test",
        url="https://example.com/",
        snippet="test snippet",
        source_domain="example.com",
        rank=1,
        provider="test",
        retrieved_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return SearchResult(**defaults)


# ── _rank_score ───────────────────────────────────────────────────────────────

def test_rank_score_first_is_highest():
    assert _rank_score(1) > _rank_score(5)


def test_rank_score_positive():
    assert _rank_score(1) > 0


def test_rank_score_decays():
    assert _rank_score(10) < _rank_score(2)


# ── _citation_boost ───────────────────────────────────────────────────────────

def test_citation_boost_zero_for_none():
    assert _citation_boost(None) == 0.0


def test_citation_boost_zero_for_zero():
    assert _citation_boost(0) == 0.0


def test_citation_boost_capped_at_015():
    assert _citation_boost(10_000_000) == pytest.approx(0.15)


def test_citation_boost_positive_for_positive():
    assert _citation_boost(100) > 0.0


# ── score_result freshness ────────────────────────────────────────────────────

def test_freshness_uses_publication_year_when_set():
    r = _result(publication_year=datetime.now(timezone.utc).year)
    scored = score_result(r, topic="general")
    assert scored.freshness_score > 0.9


def test_freshness_decays_for_old_year():
    r = _result(publication_year=2000)
    scored = score_result(r, topic="academic")
    assert scored.freshness_score <= 0.5


def test_freshness_news_uses_tight_window():
    recent = datetime.now(timezone.utc) - timedelta(hours=12)
    r = _result(published_date=recent)
    scored = score_result(r, topic="news")
    assert scored.freshness_score > 0.7


def test_freshness_news_penalizes_old_article():
    old = datetime.now(timezone.utc) - timedelta(days=10)
    r = _result(published_date=old)
    scored = score_result(r, topic="news")
    assert scored.freshness_score == pytest.approx(0.1)


def test_freshness_general_more_lenient_than_news():
    week_old = datetime.now(timezone.utc) - timedelta(days=7)
    r_news = score_result(_result(published_date=week_old), topic="news")
    r_gen = score_result(_result(published_date=week_old), topic="general")
    assert r_gen.freshness_score > r_news.freshness_score


def test_freshness_defaults_to_half_when_no_date():
    r = _result()
    scored = score_result(r, topic="general")
    assert scored.freshness_score == pytest.approx(0.5)


def test_trust_score_set_by_score_result():
    r = _result(source_domain="nature.com")
    scored = score_result(r, topic="academic")
    assert 0.0 <= scored.trust_score <= 1.0
