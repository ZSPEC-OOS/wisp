from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from apps.api.dependencies import auth


# ── Pure-function tests ───────────────────────────────────────────────────────

def test_parse_api_keys_trims_and_ignores_empty_entries():
    keys = auth._parse_api_keys(" alpha,, beta ,  ,gamma")
    assert keys == {"alpha", "beta", "gamma"}


def test_validate_api_key_format_rejects_short():
    assert not auth.validate_api_key_format("short")


def test_validate_api_key_format_rejects_whitespace():
    assert not auth.validate_api_key_format("has a space in it!1A")


def test_validate_api_key_format_rejects_single_char_class():
    # lowercase only — only one character class, must fail
    assert not auth.validate_api_key_format("alllowercaseonlyabcd")


def test_validate_api_key_format_accepts_strong_key():
    assert auth.validate_api_key_format("StrongKey!42abcde")


# ── In-memory lockout tests ───────────────────────────────────────────────────

def _fresh_state():
    """Return clean copies of the lockout dicts to isolate tests."""
    return {}, {}


def test_lockout_not_triggered_below_threshold():
    attempts, by_ip = {}, {}
    now = time.monotonic()
    ident = "testkey1"
    # 4 failures — one below threshold of 5
    for _ in range(4):
        pruned = auth._prune(attempts.get(ident, []) + [time.monotonic()], time.monotonic())
        attempts[ident] = pruned

    # Manually call _is_locked_out_local with patched dicts
    import apps.api.dependencies.auth as _auth_mod
    original_fa, original_bip = _auth_mod._failed_attempts, _auth_mod._failed_by_ip
    _auth_mod._failed_attempts = attempts
    _auth_mod._failed_by_ip = by_ip
    try:
        assert not _auth_mod._is_locked_out_local(ident, None)
    finally:
        _auth_mod._failed_attempts = original_fa
        _auth_mod._failed_by_ip = original_bip


def test_lockout_triggered_at_threshold(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})

    ident = "testkey2"
    for _ in range(auth._LOCKOUT_THRESHOLD):
        _auth_mod._record_failure_local(ident, None)

    assert _auth_mod._is_locked_out_local(ident, None)


def test_lockout_clears_stale_entries(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})

    ident = "stale_key"
    # Seed with timestamps older than the lockout window
    old_time = time.monotonic() - auth._LOCKOUT_WINDOW - 1
    _auth_mod._failed_attempts[ident] = [old_time] * auth._LOCKOUT_THRESHOLD

    # Should not be locked out — entries are stale
    assert not _auth_mod._is_locked_out_local(ident, None)


def test_ip_lockout_independent_of_key_lockout(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})

    ip = "10.0.0.1"
    # Drive IP over threshold
    for _ in range(auth._IP_LOCKOUT_THRESHOLD):
        _auth_mod._record_failure_local("anon", ip)

    assert _auth_mod._is_locked_out_local("fresh_key", ip)
    assert not _auth_mod._is_locked_out_local("fresh_key", "10.0.0.2")


def test_empty_list_removed_after_all_expire(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})

    ident = "cleanup_key"
    old_time = time.monotonic() - auth._LOCKOUT_WINDOW - 1
    _auth_mod._failed_attempts[ident] = [old_time]

    # Recording a new failure should prune the stale entry and not accumulate empties
    _auth_mod._record_failure_local(ident, None)
    assert ident in _auth_mod._failed_attempts
    assert len(_auth_mod._failed_attempts[ident]) == 1  # only the new one remains


# ── Guard factory tests (calling dependency directly) ─────────────────────────

def _mock_request(ip: str = "127.0.0.1") -> MagicMock:
    req = MagicMock()
    req.client.host = ip
    return req


async def test_guard_allows_when_no_keys_configured(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod.settings, "api_keys", "")
    guard = auth.api_key_guard_factory()
    # Should return without raising — no keys configured
    await guard(request=_mock_request(), x_api_key=None)


async def test_guard_blocks_missing_key(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod.settings, "api_keys", "StrongKey!42abcde")
    monkeypatch.setattr(_auth_mod.settings, "redis_url", "")
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})
    guard = auth.api_key_guard_factory()
    with pytest.raises(HTTPException) as exc_info:
        await guard(request=_mock_request(), x_api_key=None)
    assert exc_info.value.status_code == 401


async def test_guard_blocks_wrong_key(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod.settings, "api_keys", "StrongKey!42abcde")
    monkeypatch.setattr(_auth_mod.settings, "redis_url", "")
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})
    guard = auth.api_key_guard_factory()
    with pytest.raises(HTTPException) as exc_info:
        await guard(request=_mock_request(), x_api_key="WrongKey!42abcde")
    assert exc_info.value.status_code == 401


async def test_guard_allows_correct_key(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    key = "StrongKey!42abcde"
    monkeypatch.setattr(_auth_mod.settings, "api_keys", key)
    monkeypatch.setattr(_auth_mod.settings, "redis_url", "")
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})
    guard = auth.api_key_guard_factory()
    # Should not raise
    await guard(request=_mock_request(), x_api_key=key)


async def test_guard_lockout_after_repeated_failures(monkeypatch):
    import apps.api.dependencies.auth as _auth_mod
    monkeypatch.setattr(_auth_mod, "_failed_attempts", {})
    monkeypatch.setattr(_auth_mod, "_failed_by_ip", {})
    monkeypatch.setattr(_auth_mod.settings, "api_keys", "StrongKey!42abcde")
    monkeypatch.setattr(_auth_mod.settings, "redis_url", "")  # force in-memory path

    guard = auth.api_key_guard_factory()
    req = _mock_request()

    # Trigger enough failures to hit lockout
    for _ in range(auth._LOCKOUT_THRESHOLD):
        try:
            await guard(request=req, x_api_key="wrong")
        except HTTPException:
            pass

    with pytest.raises(HTTPException) as exc_info:
        await guard(request=req, x_api_key="wrong")
    assert exc_info.value.status_code == 429
    assert "too_many_auth_failures" in exc_info.value.detail
