from __future__ import annotations

_TRUSTED_DOMAINS = frozenset({
    # Academic / reference
    "arxiv.org", "openalex.org", "semanticscholar.org", "doi.org",
    "reuters.com", "wikipedia.org",
    # Code / developer sources
    "github.com", "github.io",
    "stackoverflow.com", "stackexchange.com",
    "developer.mozilla.org",
    "npmjs.com", "pypi.org",
    "docs.python.org", "docs.rs", "crates.io", "pkg.go.dev",
    "learn.microsoft.com", "developer.apple.com",
    "developer.android.com", "developer.chrome.com",
    "docs.docker.com", "kubernetes.io",
})


def trust_weight(domain: str) -> float:
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return 0.9
    if any(t in domain for t in _TRUSTED_DOMAINS):
        return 0.9
    return 0.6
