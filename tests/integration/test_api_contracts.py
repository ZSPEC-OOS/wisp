from fastapi.testclient import TestClient

from apps.api.main import app


client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "WISP API"
    assert payload["docs"] == "/docs"
    assert payload["health"] == "/health"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_extract_contract_validation():
    r = client.post("/extract", json={"urls": ["https://example.com"], "format": "text"})
    assert r.status_code == 200
    payload = r.json()
    assert "documents" in payload


def test_api_key_enforcement(monkeypatch):
    from apps.api.dependencies.auth import require_api_key
    from apps.api.dependencies import auth

    test_key = "TestKey!42abcdef"
    monkeypatch.setattr(auth.settings, "api_keys", test_key)
    enforcing_guard = auth.api_key_guard_factory(parse_api_keys=lambda _: {test_key})

    try:
        app.dependency_overrides[require_api_key] = enforcing_guard

        unauthorized = client.post("/v1/search", json={"query": "wisp", "max_results": 1})
        assert unauthorized.status_code == 401

        authorized = client.post(
            "/v1/search",
            json={"query": "wisp", "max_results": 1},
            headers={"X-API-Key": test_key},
        )
        assert authorized.status_code == 200
    finally:
        app.dependency_overrides.pop(require_api_key, None)
