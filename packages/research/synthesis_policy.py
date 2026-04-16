from __future__ import annotations

from apps.api.config import settings
from packages.research.evidence import EvidenceProfile

# Multi-word terms must be checked with `in query.lower()` (not split-token match).
SYNTHESIS_TERMS = frozenset({
    "compare", "contrast", "summarize", "summary", "explain",
    "tradeoff", "tradeoffs", "pros and cons", "review", "literature",
    "analyze", "analysis", "synthesize", "differences", "advantages",
    "disadvantages", "consensus", "difference between", "versus", "vs",
})


def synthesis_intent_score(query: str) -> float:
    """Normalised term-frequency in [0, 1].

    Uses substring matching so multi-word terms like "difference between"
    and "pros and cons" are caught without phrase-tokenisation.
    The returned score is one soft input to the gate, not a binary trigger.
    """
    tokens = query.lower().split()
    if not tokens:
        return 0.0
    hits = sum(1 for t in SYNTHESIS_TERMS if t in query.lower())
    return min(hits / max(len(tokens), 1), 1.0)


def has_clear_winner(
    scores: list[float],
    margin: float | None = None,
    ratio: float | None = None,
) -> bool:
    """True when the top passage dominates the second by both margin and ratio.

    Margin-plus-ratio is more stable than ratio alone when score scales
    compress or expand across different retrieval configurations.
    Both thresholds default to the config values so callers don't need to
    pass them explicitly for the normal production path.
    """
    _margin = margin if margin is not None else settings.llm_gate_clear_winner_margin
    _ratio  = ratio  if ratio  is not None else settings.llm_gate_clear_winner_ratio
    if len(scores) < 2:
        return True
    top1 = float(scores[0] or 0.0)
    top2 = float(scores[1] or 0.0)
    return (top1 - top2) >= _margin and top1 >= top2 * _ratio


def should_use_llm(
    query: str,
    mode: str,
    profile: EvidenceProfile,
    synthesis_mode: str = "auto",
) -> tuple[bool, str]:
    """Gate function: decide whether to invoke LLM synthesis.

    Returns (use_llm: bool, reason: str).
    The reason string is logged and emitted as a metric label for tuning.

    Decision principle:
        Use the LLM only when the request requires synthesis rather than
        direct lookup, evidence is sufficient and distributed, and no single
        passage obviously dominates.  Evidence shape is the primary signal;
        query wording and mode are secondary biases.
    """
    # ── Hard overrides ─────────────────────────────────────────────────────
    if synthesis_mode == "never":
        return False, "request_never"

    if synthesis_mode == "always":
        if profile.evidence_count == 0:
            return False, "no_evidence"
        return True, "request_always"

    # ── Minimum evidence floor ──────────────────────────────────────────────
    if profile.evidence_count < 2:
        return False, "insufficient_evidence"

    # ── Conflicting sparse evidence — native path is safer ──────────────────
    if profile.likely_conflict and profile.evidence_count < 4:
        return False, "conflicting_sparse_evidence"

    intent     = synthesis_intent_score(query)
    has_intent = intent >= settings.llm_gate_synthesis_intent_threshold

    # ── Fast path: single dominant passage, simple concise lookup ───────────
    if profile.has_clear_winner and mode == "concise" and not has_intent:
        return False, "clear_winner_lookup"

    # ── Synthesis intent + multi-source evidence ────────────────────────────
    if has_intent and profile.source_count >= 2:
        return True, "query_requires_synthesis"

    # ── Report / structured: evidence shape dominates, mode is a bias only ──
    if mode in {"report", "structured"}:
        if profile.source_count >= 2 and not profile.has_clear_winner:
            return True, "report_with_distributed_evidence"
        if (
            profile.provider_count >= 2
            and profile.confidence_score >= settings.llm_gate_report_min_confidence
        ):
            return True, "report_with_source_diversity"

    # ── Multi-chunk distributed evidence without dominant passage ───────────
    if (
        profile.evidence_count >= 4
        and not profile.has_clear_winner
        and profile.confidence_score >= settings.llm_gate_min_confidence
    ):
        return True, "multi_chunk_distributed_evidence"

    return False, "native_fast_path"
