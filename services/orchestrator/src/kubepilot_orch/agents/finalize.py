"""Finalize node — stamps the terminal state after RCA completes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from kubepilot_orch.state import InvestigationState

log = structlog.get_logger(__name__)


async def finalize_node(state: InvestigationState) -> dict[str, Any]:
    update: dict[str, Any] = {
        "current_step": "completed",
        "finished_at": datetime.now(UTC),
    }
    if state.rca is not None:
        update["confidence"] = state.rca.confidence
    log.info(
        "investigation_completed",
        incident_id=str(state.incident_id),
        confidence=update.get("confidence"),
        evidence_count=len(state.evidence),
    )
    return update
