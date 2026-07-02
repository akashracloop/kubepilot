"""API-key auth with light multi-tenancy (Phase 2).

A presented key resolves to a ``Principal`` carrying a role and an allowed
namespace set. Roles: ``viewer`` (read-only) and ``investigator`` (may trigger
investigations). Namespaces empty = all.

Resolution order:
1. No auth configured at all (no api_key, no keys) → open dev mode: an
   investigator with access to all namespaces.
2. Key present in ``auth.keys`` → that policy.
3. Key equals the legacy ``auth.api_key`` → investigator, all namespaces.
4. Otherwise → 401.

Phase 3 swaps this for OIDC / Keycloak with real RBAC.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from kubepilot_api.config import ApiSettings


class Principal(BaseModel):
    role: str
    namespaces: list[str]  # empty = all namespaces

    def can_investigate(self) -> bool:
        return self.role == "investigator"

    def allows_namespace(self, namespace: str) -> bool:
        return not self.namespaces or namespace in self.namespaces


_OPEN_DEV = Principal(role="investigator", namespaces=[])


def make_principal_dep(settings: ApiSettings):  # type: ignore[no-untyped-def]
    """Return a FastAPI dependency that resolves the API key to a ``Principal``."""
    header_name = settings.auth.api_key_header
    legacy_key = settings.auth.api_key
    keys = settings.auth.keys
    auth_configured = legacy_key is not None or bool(keys)

    async def _principal(
        key: str | None = Header(default=None, alias=header_name),
    ) -> Principal:
        if not auth_configured:
            return _OPEN_DEV  # dev only — no key set anywhere

        if key is not None:
            # Constant-time match against configured per-key policies.
            for candidate, policy in keys.items():
                if secrets.compare_digest(key, candidate):
                    return Principal(role=policy.role, namespaces=list(policy.namespaces))
            if legacy_key is not None and secrets.compare_digest(key, legacy_key):
                return Principal(role="investigator", namespaces=[])

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": header_name},
        )

    return _principal
