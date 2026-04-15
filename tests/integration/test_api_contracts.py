from fastapi.testclient import TestClient

from apps.api.main import app


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_extract_contract_validation():
    r = client.post("/extract", json={"urls": ["https://example.com"], "format": "text"})
    assert r.status_code == 200
    payload = r.json()
    assert "documents" in payload
