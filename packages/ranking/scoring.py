from __future__ import annotations


def trust_weight(domain: str) -> float:
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return 1.0
    if "wikipedia.org" in domain or "reuters.com" in domain:
        return 0.95
    return 0.6
