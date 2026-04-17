from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from packages.common.models import ExtractedDocument, Passage, SearchResult


class EvidenceChunk(BaseModel):
    evidence_id: str
    text: str
    url: str                          # str, not HttpUrl — avoids rejecting non-HTTP sources
    title: str | None = None
    provider: str | None = None
    rank_score: float = 0.0
    extraction_score: float | None = None
    metadata: dict[str, Any] = {}    # values must be JSON-serialisable


class EvidenceProfile(BaseModel):
    evidence_count: int
    source_count: int
    provider_count: int
    top1_score: float = 0.0
    top2_score: float = 0.0
    topk_score_sum: float = 0.0
    has_clear_winner: bool = False
    has_source_diversity: bool = False
    confidence_score: float = 0.0
    likely_conflict: bool = False     # heuristic deferred per spec §18 Q2


def build_evidence_chunks(
    ranked_passages: list[Passage],
    docs: list[ExtractedDocument],
    result_map: dict[str, SearchResult],
) -> list[EvidenceChunk]:
    """Build EvidenceChunk objects from ranked passages, binding provenance end-to-end.

    Args:
        ranked_passages: Reranked list of Passage objects (order determines evidence_id).
        docs: Extracted documents — used to look up titles.
        result_map: URL → SearchResult mapping for provider/trust metadata.
                    Should include both the canonical URL and any OA PDF URL.
    """
    doc_map = {str(d.url): d for d in docs if d.status == "ok"}
    chunks: list[EvidenceChunk] = []
    for i, p in enumerate(ranked_passages):
        url_str = str(p.source_url)
        doc = doc_map.get(url_str)
        sr  = result_map.get(url_str)
        chunks.append(EvidenceChunk(
            evidence_id=f"E{i + 1}",
            text=p.text,
            url=url_str,
            title=doc.title if doc else None,
            provider=sr.provider if sr else None,
            rank_score=p.score,
            extraction_score=p.score,
            metadata={"trust_score": float(sr.trust_score) if sr else 0.5},
        ))
    return chunks


def build_evidence_profile(
    chunks: list[EvidenceChunk],
    *,
    clear_winner_margin: float,
    clear_winner_ratio: float,
) -> EvidenceProfile:
    """Compute an EvidenceProfile from a ranked list of EvidenceChunks.

    Thresholds are injected by the caller (sourced from settings) so this
    function stays free of import-time side effects.
    """
    if not chunks:
        return EvidenceProfile(evidence_count=0, source_count=0, provider_count=0)

    scores       = [c.rank_score for c in chunks]
    source_urls  = {c.url for c in chunks}
    providers    = {c.provider for c in chunks if c.provider}

    top1 = float(scores[0]) if scores else 0.0
    top2 = float(scores[1]) if len(scores) > 1 else 0.0
    topk_sum = sum(float(s) for s in scores[:6])

    # Clear winner: margin-plus-ratio (ratio-only is unstable when scales compress)
    if len(scores) >= 2:
        clear_winner = (top1 - top2) >= clear_winner_margin and top1 >= top2 * clear_winner_ratio
    else:
        clear_winner = len(scores) == 1  # single chunk trivially dominates

    # Confidence: normalize to [0, 1] using source diversity and average trust.
    # Use additive blend to avoid the product collapsing to near-zero with few providers.
    provider_diversity = min(1.0, len(providers) / 3)  # saturates at 3 providers
    avg_trust = sum(c.metadata.get("trust_score", 0.5) for c in chunks) / len(chunks)
    confidence = round(0.4 * provider_diversity + 0.6 * avg_trust, 4)

    # Conflict heuristic: flag when multiple sources are present but scores are
    # polarised (one strong outlier among weaker ones) — a sign that sources
    # disagree rather than converge on the same answer.
    min_score = float(min(scores)) if scores else 0.0
    likely_conflict = (
        len(source_urls) >= 2
        and len(providers) >= 2
        and len(scores) >= 3
        and (top1 - min_score) > 0.35
        and top1 > top2 * 1.5
    )

    return EvidenceProfile(
        evidence_count=len(chunks),
        source_count=len(source_urls),
        provider_count=len(providers),
        top1_score=top1,
        top2_score=top2,
        topk_score_sum=topk_sum,
        has_clear_winner=clear_winner,
        has_source_diversity=len(source_urls) >= 2,
        confidence_score=confidence,
        likely_conflict=likely_conflict,
    )
