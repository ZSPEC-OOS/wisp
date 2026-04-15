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
    from apps.api.dependencies import auth

    monkeypatch.setattr(auth.settings, "api_keys", "test-key")
    unauthorized = client.post("/search", json={"query": "wisp", "max_results": 1})
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/search",
        json={"query": "wisp", "max_results": 1},
        headers={"X-API-Key": "test-key"},
    )
    assert authorized.status_code == 200

    monkeypatch.setattr(auth.settings, "api_keys", "")
