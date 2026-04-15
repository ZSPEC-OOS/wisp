from packages.common.url import canonicalize_url


def test_canonicalize_url_normalizes_case_and_query():
    url = "HTTPS://Example.com:443/path/?b=2&a=1"
    assert canonicalize_url(url) == "https://example.com/path?a=1&b=2"
