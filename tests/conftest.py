from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from apps.api.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def auth_client(client, monkeypatch):
    """TestClient that automatically sends a valid API key header."""
    from apps.api.main import app
    from apps.api.dependencies import auth
    from apps.api.dependencies.auth import require_api_key

    test_key = "test-key-12345678"
    monkeypatch.setattr(auth.settings, "api_keys", test_key)
    enforcing_guard = auth.api_key_guard_factory(parse_api_keys=lambda _: {test_key})
    app.dependency_overrides[require_api_key] = enforcing_guard
    client.headers.update({"X-API-Key": test_key})

    yield client

    client.headers.pop("X-API-Key", None)
    app.dependency_overrides.pop(require_api_key, None)
