from __future__ import annotations

import hmac
import logging
import string
import time
from collections.abc import Callable

from fastapi import Header, HTTPException, Request, status

from apps.api.config import settings

logger = logging.getLogger("wisp.auth")

# Per-key-prefix lockout
_failed_attempts: dict[str, list[float]] = {}
_LOCKOUT_WINDOW     = 60.0  # seconds
_LOCKOUT_THRESHOLD  = 5     # failures per key prefix within window

# Global per-IP lockout — stops rotating-key brute-force attacks
_failed_by_ip: dict[str, list[float]] = {}
_IP_LOCKOUT_THRESHOLD = 20  # total failures from one IP within window


def _prune(attempts: list[float], now: float) -> list[float]:
    return [t for t in attempts if now - t < _LOCKOUT_WINDOW]


def _record_failure(identifier: str, client_ip: str | None) -> None:
    now = time.monotonic()
    pruned = _prune(_failed_attempts.get(identifier, []) + [now], now)
    if pruned:
        _failed_attempts[identifier] = pruned
    elif identifier in _failed_attempts:
        del _failed_attempts[identifier]
    if client_ip:
        pruned_ip = _prune(_failed_by_ip.get(client_ip, []) + [now], now)
        if pruned_ip:
            _failed_by_ip[client_ip] = pruned_ip
        elif client_ip in _failed_by_ip:
            del _failed_by_ip[client_ip]


def _is_locked_out(identifier: str, client_ip: str | None) -> bool:
    now = time.monotonic()
    if len(_prune(_failed_attempts.get(identifier, []), now)) >= _LOCKOUT_THRESHOLD:
        return True
    if client_ip and len(_prune(_failed_by_ip.get(client_ip, []), now)) >= _IP_LOCKOUT_THRESHOLD:
        return True
    return False


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
    has_upper  = any(c.isupper() for c in key)
    has_lower  = any(c.islower() for c in key)
    has_digit  = any(c.isdigit() for c in key)
    has_symbol = any(c in string.punctuation for c in key)
    return sum([has_upper, has_lower, has_digit, has_symbol]) >= 2


def api_key_guard_factory(parse_api_keys: Callable[[str], set[str]] = _parse_api_keys):
    accepted_keys = parse_api_keys(settings.api_keys) if settings.api_keys else set()

    async def require_api_key(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> None:
        if not accepted_keys:
            return

        client_ip  = request.client.host if request.client else None
        key_provided = x_api_key or ""
        identifier   = key_provided[:8] or "anonymous"

        if _is_locked_out(identifier, client_ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too_many_auth_failures",
                headers={"Retry-After": "60"},
            )

        key_valid = any(hmac.compare_digest(key_provided, k) for k in accepted_keys)
        if not key_provided or not key_valid:
            _record_failure(identifier, client_ip)
            logger.warning(
                "auth_failed",
                extra={"key_prefix": key_provided[:4] if key_provided else None,
                       "client_ip": client_ip},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_or_missing_api_key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        logger.info("auth_ok")

    return require_api_key


require_api_key = api_key_guard_factory()
