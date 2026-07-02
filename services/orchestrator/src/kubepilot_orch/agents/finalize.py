"""Finalize node — stamps the terminal state after RCA completes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from kubepilot_orch.state import InvestigationState

log = structlog.get_logger(__name__)


async def finalize_node(state: InvestigationState) -> dict[str, Any]:
    total_tokens = sum(output.tokens_used for output in state.agent_outputs.values())
    update: dict[str, Any] = {
        "current_step": "completed",
        "finished_at": datetime.now(UTC),
        "total_tokens_used": total_tokens,
    }
    if state.rca is not None:
        update["confidence"] = state.rca.confidence
    log.info(
        "investigation_completed",
        incident_id=str(state.incident_id),
        confidence=update.get("confidence"),
        evidence_count=len(state.evidence),
    )
    # AgentOps ledger line — token cost per investigation (persisted in state too).
    log.info(
        "investigation_cost",
        incident_id=str(state.incident_id),
        total_tokens=total_tokens,
        agents=len(state.agent_outputs),
    )
    return update
