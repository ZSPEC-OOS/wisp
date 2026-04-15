from __future__ import annotations

from collections.abc import Callable

from fastapi import Header, HTTPException, status

from apps.api.config import settings


def _normalize_key(key: str) -> str:
    return key.strip()


def _parse_api_keys(raw_keys: str) -> set[str]:
    return {_normalize_key(key) for key in raw_keys.split(",") if _normalize_key(key)}


def is_auth_enabled() -> bool:
    return bool(settings.api_keys and _parse_api_keys(settings.api_keys))


def api_key_guard_factory(parse_api_keys: Callable[[str], set[str]] = _parse_api_keys):
    async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
        if not settings.api_keys:
            return

        accepted_keys = parse_api_keys(settings.api_keys)
        if not accepted_keys:
            return

        if not x_api_key or x_api_key not in accepted_keys:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_or_missing_api_key",
            )

    return require_api_key


require_api_key = api_key_guard_factory()
