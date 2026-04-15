from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=False)))
    return urlunparse((scheme, netloc, path, "", query, ""))


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()
