"""Audit-log export (RBAC v2, Phase 3).

Every access-controlled action emits a structured **audit event** — actor, role,
action, resource, namespace, and the allow/deny decision. Events go through
structlog, which the AgentOps OTel pipeline exports to a SIEM (the same OTLP
exporter the rest of the gateway uses). Denials are audited too: a
namespace-scoped principal reaching for another namespace produces a ``denied``
record, which is exactly what a security team wants to see.

Keep this dependency-light and synchronous — an audit emit must never fail a
request; it's a log call, not a network round-trip on the hot path.
"""

from __future__ import annotations

from typing import Any

import structlog

# A dedicated logger name so the OTel/SIEM pipeline can route audit events
# separately from ordinary application logs.
_audit_log = structlog.get_logger("kubepilot.audit")


def emit_audit(
    *,
    actor_role: str,
    action: str,
    resource: str,
    decision: str,  # "allowed" | "denied"
    namespace: str | None = None,
    **extra: Any,
) -> None:
    """Emit one audit event. ``decision`` records the authz outcome."""
    _audit_log.info(
        "audit",
        audit=True,
        actor_role=actor_role,
        action=action,
        resource=resource,
        namespace=namespace,
        decision=decision,
        **extra,
    )
