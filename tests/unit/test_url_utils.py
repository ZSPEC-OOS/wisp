from __future__ import annotations

import pytest

from packages.common.url import canonicalize_url, validate_safe_url


# ── canonicalize_url ──────────────────────────────────────────────────────────

def test_canonicalize_url_normalizes_case_and_query():
    url = "HTTPS://Example.com:443/path/?b=2&a=1"
    assert canonicalize_url(url) == "https://example.com/path?a=1&b=2"


def test_canonicalize_removes_default_http_port():
    assert canonicalize_url("http://example.com:80/page") == "http://example.com/page"


def test_canonicalize_removes_default_https_port():
    assert canonicalize_url("https://example.com:443/") == "https://example.com/"


def test_canonicalize_strips_trailing_slash_from_path():
    assert canonicalize_url("https://example.com/path/") == "https://example.com/path"


def test_canonicalize_keeps_root_slash():
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_canonicalize_sorts_query_params():
    result = canonicalize_url("https://example.com/?z=last&a=first")
    assert result == "https://example.com/?a=first&z=last"


# ── validate_safe_url — allowed ───────────────────────────────────────────────

def test_valid_https_url_passes():
    assert validate_safe_url("https://example.com/page") == "https://example.com/page"


def test_valid_http_url_passes():
    assert validate_safe_url("http://example.com/") == "http://example.com/"


# ── validate_safe_url — blocked schemes ──────────────────────────────────────

@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<script>",
])
def test_non_http_schemes_blocked(url):
    with pytest.raises(ValueError, match="scheme"):
        validate_safe_url(url)


# ── validate_safe_url — loopback / metadata hosts ────────────────────────────

@pytest.mark.parametrize("url", [
    "http://localhost/",
    "http://127.0.0.1/",
    "http://0.0.0.0/",
    "http://169.254.169.254/latest/meta-data/",       # AWS IMDS
    "http://100.100.100.200/",                          # Alibaba IMDS
    "http://metadata.google.internal/computeMetadata/v1/",
])
def test_metadata_and_loopback_hosts_blocked(url):
    with pytest.raises(ValueError):
        validate_safe_url(url)


# ── validate_safe_url — private IP ranges ────────────────────────────────────

@pytest.mark.parametrize("ip,url_ip", [
    ("10.0.0.1", "10.0.0.1"),
    ("172.16.0.1", "172.16.0.1"),
    ("192.168.1.1", "192.168.1.1"),
    ("::1", "[::1]"),          # IPv6 loopback — brackets required in URL
    ("fc00::1", "[fc00::1]"),  # IPv6 unique local
])
def test_private_ip_addresses_blocked(ip, url_ip):
    with pytest.raises(ValueError):
        validate_safe_url(f"http://{url_ip}/")


# ── validate_safe_url — private hostname patterns ────────────────────────────

@pytest.mark.parametrize("host", [
    "myserver.local",
    "api.internal",
    "db.localdomain",
    "service.corp",
    "router.home",
    "gateway.lan",
    "host.intranet",
])
def test_private_hostnames_blocked(host):
    with pytest.raises(ValueError):
        validate_safe_url(f"http://{host}/")
