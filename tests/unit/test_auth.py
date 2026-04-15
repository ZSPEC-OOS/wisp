from fastapi import HTTPException

from apps.api.dependencies import auth


def test_parse_api_keys_trims_and_ignores_empty_entries():
    keys = auth._parse_api_keys(" alpha,, beta ,  ,gamma")
    assert keys == {"alpha", "beta", "gamma"}


def test_require_api_key_allows_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_keys", "")
    guard = auth.api_key_guard_factory()

    import asyncio

    asyncio.run(guard(None))


def test_require_api_key_blocks_invalid_key(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_keys", "secret-key")
    guard = auth.api_key_guard_factory()

    import asyncio

    try:
        asyncio.run(guard("wrong"))
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "invalid_or_missing_api_key"


def test_require_api_key_allows_valid_key(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_keys", "secret-key")
    guard = auth.api_key_guard_factory()

    import asyncio

    asyncio.run(guard("secret-key"))
