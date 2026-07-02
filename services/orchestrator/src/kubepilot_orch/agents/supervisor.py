"""Supervisor — Phase 1 plan node.

In Phase 1 the supervisor's job is intentionally small: stamp the investigation
as "in progress" and let the static graph wiring fan out to all three
specialist agents. Phase 3 introduces conditional routing (skip the metrics
agent when Prometheus is unreachable, etc.) — this node becomes the place where
that decision is made.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from kubepilot_orch.state import InvestigationState

log = structlog.get_logger(__name__)

AGENT_NAME = "supervisor"


async def supervisor_node(state: InvestigationState) -> dict[str, Any]:
    log.info(
        "investigation_planned",
        incident_id=str(state.incident_id),
        namespace=state.namespace,
        service=state.service,
    )
    update: dict[str, Any] = {"current_step": "investigating"}
    # If the caller didn't pre-populate started_at, do it here so finalize
    # can compute duration.
    if state.started_at is None:  # type: ignore[unreachable]
        update["started_at"] = datetime.now(UTC)
    return update
