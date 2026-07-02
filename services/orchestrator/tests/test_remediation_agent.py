"""Remediation agent — plan generation, catalog filtering, fail-safe (Phase 4 W4)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from kubepilot_orch.agents import remediation_agent
from kubepilot_orch.state import (
    AgentOutput,
    InvestigationState,
    RCAReport,
    Recommendation,
    ServiceKnowledge,
)
from kubepilot_orch.testing import ScriptedLLM, build_router, llm_text


def _state(rca: RCAReport | None) -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
        query="why is checkout-service slow?",
        namespace="prod",
        service="checkout-service",
        rca=rca,
        recommendations=[
            Recommendation(title="Roll back v2.3.1", rationale="revert the N+1 query")
        ],
        knowledge_context=[ServiceKnowledge(service="checkout-service", owner="payments-team")],
        agent_outputs={"rca": AgentOutput(agent_name="rca", succeeded=True)},
        started_at=datetime(2026, 7, 2, 10, 7, tzinfo=UTC),
    )


_RCA = RCAReport(
    root_cause="Deploy v2.3.1 added an N+1 query.",
    root_cause_category="DeploymentRegression",
    confidence=0.88,
    reasoning="deploy correlates with the latency spike",
    recommendations=["Roll back to v2.3.0"],
)


@pytest.mark.asyncio
async def test_produces_plan_with_catalog_filled_reversibility() -> None:
    out = {
        "actions": [
            {
                "tool": "rollout_undo",
                "target": "deployment/checkout-service",
                "namespace": "prod",
                "arguments": {"to_revision": 4},
                "rationale": "Revert the regressive deploy.",
                "priority": 1,
            }
        ]
    }
    scripted = ScriptedLLM(responses=[llm_text(json.dumps(out))])
    plan = await remediation_agent.run(_state(_RCA), llm=build_router(scripted))

    assert len(plan.actions) == 1
    a = plan.actions[0]
    assert a.tool == "rollout_undo"
    assert a.reversibility == "reversible"  # code-filled from the catalog
    assert a.approval_tier == "operator"
    assert a.arguments == {"to_revision": 4}
    assert plan.generated_at is not None


@pytest.mark.asyncio
async def test_drops_actions_outside_the_catalog() -> None:
    out = {
        "actions": [
            {"tool": "delete_namespace", "target": "prod", "namespace": "prod", "priority": 1},
            {
                "tool": "rollout_restart",
                "target": "deployment/checkout-service",
                "namespace": "prod",
                "priority": 2,
            },
        ]
    }
    scripted = ScriptedLLM(responses=[llm_text(json.dumps(out))])
    plan = await remediation_agent.run(_state(_RCA), llm=build_router(scripted))
    # The forbidden tool is dropped; only the catalog action survives.
    assert [a.tool for a in plan.actions] == ["rollout_restart"]


@pytest.mark.asyncio
async def test_ranks_by_priority() -> None:
    out = {
        "actions": [
            {
                "tool": "scale",
                "target": "deployment/x",
                "namespace": "prod",
                "arguments": {"replicas": 5},
                "priority": 3,
            },
            {"tool": "rollout_undo", "target": "deployment/x", "namespace": "prod", "priority": 1},
        ]
    }
    scripted = ScriptedLLM(responses=[llm_text(json.dumps(out))])
    plan = await remediation_agent.run(_state(_RCA), llm=build_router(scripted))
    assert [a.tool for a in plan.actions] == ["rollout_undo", "scale"]


@pytest.mark.asyncio
async def test_no_rca_gives_empty_plan() -> None:
    scripted = ScriptedLLM(responses=[])  # must not be called
    plan = await remediation_agent.run(_state(None), llm=build_router(scripted))
    assert plan.actions == []


@pytest.mark.asyncio
async def test_invalid_output_fails_safe_to_empty_plan() -> None:
    scripted = ScriptedLLM(responses=[llm_text("not json")])
    plan = await remediation_agent.run(_state(_RCA), llm=build_router(scripted))
    assert plan.actions == []
    assert "failed" in (plan.notes or "")


def test_to_state_update_marks_pending_approval_only_with_actions() -> None:
    from kubepilot_orch.state import RemediationAction, RemediationPlan

    empty = remediation_agent.to_state_update(RemediationPlan(actions=[]))
    assert empty["remediation_outcome"] is None

    withact = remediation_agent.to_state_update(
        RemediationPlan(
            actions=[RemediationAction(tool="scale", target="deployment/x", namespace="prod")]
        )
    )
    assert withact["remediation_outcome"] == "pending_approval"
    assert withact["completed_agents"] == ["remediation"]
