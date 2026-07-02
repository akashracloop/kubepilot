"""AgentOps: token-cost ledger + tracing setup (no-op paths)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from kubepilot_orch.agents.finalize import finalize_node
from kubepilot_orch.observability import setup_tracing
from kubepilot_orch.state import AgentOutput, InvestigationState, RCAReport


def _state_with_tokens() -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.uuid4(),
        query="why is payment-service failing?",
        namespace="prod",
        started_at=datetime.now(UTC),
        agent_outputs={
            "kubernetes": AgentOutput(agent_name="kubernetes", succeeded=True, tokens_used=1200),
            "metrics": AgentOutput(agent_name="metrics", succeeded=True, tokens_used=800),
            "logs": AgentOutput(agent_name="logs", succeeded=True, tokens_used=1000),
        },
        rca=RCAReport(root_cause="OOMKilled", confidence=0.9, reasoning="..."),
    )


async def test_finalize_sums_token_ledger() -> None:
    update = await finalize_node(_state_with_tokens())
    assert update["total_tokens_used"] == 3000
    assert update["current_step"] == "completed"
    assert update["confidence"] == 0.9
    assert update["finished_at"] is not None


async def test_finalize_zero_tokens_when_no_agent_outputs() -> None:
    state = InvestigationState(
        incident_id=uuid.uuid4(),
        query="q",
        namespace="prod",
        started_at=datetime.now(UTC),
    )
    update = await finalize_node(state)
    assert update["total_tokens_used"] == 0


def test_setup_tracing_noops_without_endpoint() -> None:
    # No endpoint configured → tracing stays off, no crash, returns False.
    assert setup_tracing("kubepilot-api", None) is False
    assert setup_tracing("kubepilot-api", "") is False
