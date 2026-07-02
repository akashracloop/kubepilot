"""Static API-key auth dependency.

Phase 1: a single shared secret loaded from settings (k8s Secret in prod).
Phase 3 swaps this for OIDC / Keycloak with roles.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from kubepilot_api.config import ApiSettings


def make_api_key_dep(settings: ApiSettings):  # type: ignore[no-untyped-def]
    """Return a FastAPI dependency callable validating the configured API key.

    If ``settings.auth.api_key`` is None, the dependency is a no-op — intended
    for local dev only. Production deployments MUST set the key.
    """
    expected = settings.auth.api_key
    header_name = settings.auth.api_key_header

    async def _require_key(
        key: str | None = Header(default=None, alias=header_name),
    ) -> None:
        if expected is None:
            return
        if key is None or not secrets.compare_digest(key, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": header_name},
            )

    return _require_key
