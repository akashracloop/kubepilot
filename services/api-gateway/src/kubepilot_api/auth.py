"""API-key auth with role-based access control (RBAC v2, Phase 3).

A presented key resolves to a ``Principal`` carrying a **role** and an allowed
namespace set. The role hierarchy (ascending privilege):

    viewer < investigator < operator < admin

- **viewer** — read investigations (within scope).
- **investigator** — viewer + trigger investigations (within scope).
- **operator** — investigator + sees **every** namespace (namespace scoping is
  lifted; an on-call operator needs the whole cluster picture).
- **admin** — operator + administrative endpoints (key/config management).

Namespace-scoped tokens: ``namespaces`` restricts viewer/investigator to those
namespaces; operator/admin ignore the scope (see everything). Empty = all.

Resolution order:
1. No auth configured (no api_key, no keys) → open dev mode (investigator, all ns).
2. Key present in ``auth.keys`` → that policy.
3. Key equals the legacy ``auth.api_key`` → investigator, all namespaces.
4. Otherwise → 401.

OIDC/Keycloak is the planned opt-in backend (issue short-lived tokens that map to
the same ``Principal`` shape); the static-token path here stays primary.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from kubepilot_api.config import ApiSettings

# Role privilege ranks. Unknown roles rank below viewer (deny by default).
ROLE_RANK: dict[str, int] = {
    "viewer": 0,
    "investigator": 1,
    "operator": 2,
    "admin": 3,
}

# Operator and above transcend namespace scoping — they see the whole cluster.
_CLUSTER_WIDE_RANK = ROLE_RANK["operator"]


class Principal(BaseModel):
    role: str
    namespaces: list[str]  # empty = all namespaces

    @property
    def rank(self) -> int:
        return ROLE_RANK.get(self.role, -1)

    def has_role(self, minimum: str) -> bool:
        """True when this principal's role is at least ``minimum`` in the hierarchy."""
        return self.rank >= ROLE_RANK.get(minimum, 999)

    def can_view(self) -> bool:
        return self.has_role("viewer")

    def can_investigate(self) -> bool:
        return self.has_role("investigator")

    def is_admin(self) -> bool:
        return self.has_role("admin")

    def allows_namespace(self, namespace: str) -> bool:
        # Operator/admin see everything; scoped roles are limited to their set.
        if self.rank >= _CLUSTER_WIDE_RANK:
            return True
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
