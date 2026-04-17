from __future__ import annotations

import ipaddress
import re
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


# ── SSRF prevention ───────────────────────────────────────────────────────────

_BLOCKED_HOSTS: frozenset[str] = frozenset({
    "localhost",
    "0.0.0.0",
    "169.254.169.254",   # AWS/GCP/Azure IMDS
    "100.100.100.200",   # Alibaba Cloud metadata
    "metadata.google.internal",
    "metadata.google",
    "computemetadata",
})

# Rejects *.local, *.internal, *.localdomain, *.intranet
_PRIVATE_HOSTNAME_RE = re.compile(
    r"^(.+\.)?(local|internal|localdomain|intranet|corp|lan|home)$",
    re.IGNORECASE,
)


def _is_private_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )
    except ValueError:
        return False


def validate_safe_url(url: str) -> str:
    """Raise ValueError if the URL could be used for an SSRF attack.

    Checks:
    - scheme must be http or https
    - host must not be blank
    - host must not be a known metadata/loopback endpoint
    - host must not match private-network hostname patterns
    - host must not be a private/reserved IP address
    """
    parsed = urlparse(str(url))

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme {parsed.scheme!r} is not allowed — use http or https")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL has no host")

    if host in _BLOCKED_HOSTS:
        raise ValueError(f"URL targets a reserved host: {host!r}")

    if _PRIVATE_HOSTNAME_RE.match(host):
        raise ValueError(f"URL targets a private hostname: {host!r}")

    if _is_private_ip(host):
        raise ValueError(f"URL targets a private or reserved IP address: {host!r}")

    return url
