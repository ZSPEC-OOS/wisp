from __future__ import annotations

import hmac
import logging
import string
from collections.abc import Callable

from fastapi import Header, HTTPException, status

from apps.api.config import settings

logger = logging.getLogger("wisp.auth")


def _normalize_key(key: str) -> str:
    return key.strip()


def _parse_api_keys(raw_keys: str) -> set[str]:
    return {_normalize_key(key) for key in raw_keys.split(",") if _normalize_key(key)}


def is_auth_enabled() -> bool:
    return bool(settings.api_keys and _parse_api_keys(settings.api_keys))


def validate_api_key_format(key: str) -> bool:
    """Return True if key meets minimum security requirements."""
    return (
        len(key) >= 16
        and all(c in string.printable and c not in string.whitespace for c in key)
    )


def api_key_guard_factory(parse_api_keys: Callable[[str], set[str]] = _parse_api_keys):
    async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
        if not settings.api_keys:
            return

        accepted_keys = parse_api_keys(settings.api_keys)
        if not accepted_keys:
            return

        key_provided = x_api_key or ""
        key_valid = any(hmac.compare_digest(key_provided, k) for k in accepted_keys)

        if not key_provided or not key_valid:
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
