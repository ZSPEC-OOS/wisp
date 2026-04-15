from __future__ import annotations

_TRUSTED_DOMAINS = frozenset({
    "arxiv.org", "openalex.org", "semanticscholar.org", "doi.org",
    "reuters.com", "wikipedia.org",
})


def trust_weight(domain: str) -> float:
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return 0.9
    if any(t in domain for t in _TRUSTED_DOMAINS):
        return 0.9
    return 0.6
