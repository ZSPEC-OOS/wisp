from __future__ import annotations

import hmac
import logging
import string
import time
from collections.abc import Callable

from fastapi import Header, HTTPException, status

from apps.api.config import settings

logger = logging.getLogger("wisp.auth")

# Failed-auth lockout: track timestamps of failures per identifier
_failed_attempts: dict[str, list[float]] = {}
_LOCKOUT_WINDOW = 60.0   # seconds
_LOCKOUT_THRESHOLD = 5   # failures within window → 429


def _record_failure(identifier: str) -> None:
    now = time.monotonic()
    attempts = _failed_attempts.setdefault(identifier, [])
    attempts.append(now)
    # Prune old entries
    _failed_attempts[identifier] = [t for t in attempts if now - t < _LOCKOUT_WINDOW]


def _is_locked_out(identifier: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failed_attempts.get(identifier, []) if now - t < _LOCKOUT_WINDOW]
    return len(recent) >= _LOCKOUT_THRESHOLD


def _normalize_key(key: str) -> str:
    return key.strip()


def _parse_api_keys(raw_keys: str) -> set[str]:
    return {_normalize_key(key) for key in raw_keys.split(",") if _normalize_key(key)}


def is_auth_enabled() -> bool:
    return bool(settings.api_keys and _parse_api_keys(settings.api_keys))


def validate_api_key_format(key: str) -> bool:
    """Return True if key meets minimum security requirements."""
    if len(key) < 16:
        return False
    if not all(c in string.printable and c not in string.whitespace for c in key):
        return False
    # Require at least 2 distinct character classes for basic entropy
    has_upper = any(c.isupper() for c in key)
    has_lower = any(c.islower() for c in key)
    has_digit = any(c.isdigit() for c in key)
    has_symbol = any(c in string.punctuation for c in key)
    return sum([has_upper, has_lower, has_digit, has_symbol]) >= 2


def api_key_guard_factory(parse_api_keys: Callable[[str], set[str]] = _parse_api_keys):
    async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
        if not settings.api_keys:
            return

        accepted_keys = parse_api_keys(settings.api_keys)
        if not accepted_keys:
            return

        key_provided = x_api_key or ""
        identifier = key_provided[:8] or "anonymous"

        if _is_locked_out(identifier):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too_many_auth_failures",
                headers={"Retry-After": "60"},
            )

        key_valid = any(hmac.compare_digest(key_provided, k) for k in accepted_keys)

        if not key_provided or not key_valid:
            _record_failure(identifier)
            logger.warning(
                "auth_failed",
                extra={"key_prefix": key_provided[:4] if key_provided else None, "detail": "invalid_or_missing_api_key"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_or_missing_api_key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        logger.info("auth_ok")

    return require_api_key


require_api_key = api_key_guard_factory()
