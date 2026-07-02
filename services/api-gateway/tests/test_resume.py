"""Resume-after-approval loop (Phase 4 gap fix).

Proves the orchestrator client's interrupt handling against a *real* LangGraph
compiled with a MemorySaver + ``interrupt_before``: an investigation with a
remediation plan parks at ``pending_approval``, and ``start_resume`` injects the
recorded approvals into the checkpoint and drives the graph to completion — so
approve → execute and reject → resolve both actually reach ``finalize``.

The scripted ``_FakeGraph`` in test_approvals.py has no ``aget_state`` /
``aupdate_state``, so the approval endpoints there stay a pure record-only path;
here we exercise the genuine checkpoint resume.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from kubepilot_api.orchestrator_client import InvestigationOrchestrator
from kubepilot_api.pubsub import InvestigationBus
from kubepilot_api.repository import (
    COMPLETED,
    PENDING_APPROVAL,
    InMemoryInvestigationRepository,
    InvestigationRecord,
)
from kubepilot_orch.state import (
    Approval,
    InvestigationState,
    RemediationAction,
    RemediationPlan,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph


def _build_interrupt_graph(*, plan_actions: int) -> Any:
    """A minimal graph mirroring the Phase 4 shape: plan → [interrupt] → execute."""

    async def remediation_node(state: InvestigationState) -> dict[str, Any]:
        actions = [
            RemediationAction(
                tool="rollout_undo",
                target=f"deployment/checkout-{i}",
                namespace="prod",
                reversibility="reversible",
                approval_tier="operator",
            )
            for i in range(plan_actions)
        ]
        return {
            "remediation_plan": RemediationPlan(actions=actions),
            "remediation_outcome": "pending_approval" if actions else None,
            "current_step": "remediation_planned",
        }

    async def execute_node(state: InvestigationState) -> dict[str, Any]:
        approved = [a for a in state.approvals if a.decision == "approved"]
        rejected = [a for a in state.approvals if a.decision == "rejected"]
        if not state.remediation_plan or not state.remediation_plan.actions:
            outcome = "closed"
        elif rejected:
            outcome = "rejected"
        elif approved:
            outcome = "closed"
        else:
            outcome = "pending_approval"
        return {"remediation_outcome": outcome, "current_step": "remediation_executed"}

    g = StateGraph(InvestigationState)
    g.add_node("remediation", remediation_node)
    g.add_node("execute_remediation", execute_node)
    g.add_edge(START, "remediation")
    g.add_edge("remediation", "execute_remediation")
    g.add_edge("execute_remediation", END)
    return g.compile(checkpointer=MemorySaver(), interrupt_before=["execute_remediation"])


async def _seed(
    orch: InvestigationOrchestrator, repo: InMemoryInvestigationRepository
) -> uuid.UUID:
    incident = uuid.uuid4()
    state = InvestigationState(
        incident_id=incident,
        query="why slow?",
        namespace="prod",
        started_at=datetime.now(UTC),
    )
    await repo.create(InvestigationRecord.from_initial(incident, "why slow?", "prod", None, state))
    orch.start_investigation(state)
    await orch.wait_for(incident, timeout=5)
    return incident


async def _record_decision(
    repo: InMemoryInvestigationRepository, incident: uuid.UUID, decision: str, outcome: str
) -> None:
    rec = await repo.get(incident)
    assert rec is not None
    state = InvestigationState.model_validate(rec.state_json)
    state.approvals.append(Approval(action_index=0, decision=decision, approver_role="operator"))
    state.remediation_outcome = outcome
    await repo.update_state(incident, state)


@pytest.mark.asyncio
async def test_investigation_parks_pending_approval() -> None:
    repo = InMemoryInvestigationRepository()
    orch = InvestigationOrchestrator(
        compiled_graph=_build_interrupt_graph(plan_actions=1), repo=repo, bus=InvestigationBus()
    )
    incident = await _seed(orch, repo)

    rec = await repo.get(incident)
    assert rec is not None
    assert rec.status == PENDING_APPROVAL
    state = InvestigationState.model_validate(rec.state_json)
    assert state.remediation_plan is not None
    assert len(state.remediation_plan.actions) == 1


@pytest.mark.asyncio
async def test_resume_after_approval_executes() -> None:
    repo = InMemoryInvestigationRepository()
    orch = InvestigationOrchestrator(
        compiled_graph=_build_interrupt_graph(plan_actions=1), repo=repo, bus=InvestigationBus()
    )
    incident = await _seed(orch, repo)
    assert (await repo.get(incident)).status == PENDING_APPROVAL  # type: ignore[union-attr]

    await _record_decision(repo, incident, "approved", "approved")
    orch.start_resume(incident)
    await orch.wait_for(incident, timeout=5)

    rec = await repo.get(incident)
    assert rec is not None
    assert rec.status == COMPLETED
    final = InvestigationState.model_validate(rec.state_json)
    assert final.remediation_outcome == "closed"


@pytest.mark.asyncio
async def test_resume_after_rejection_resolves_without_execution() -> None:
    repo = InMemoryInvestigationRepository()
    orch = InvestigationOrchestrator(
        compiled_graph=_build_interrupt_graph(plan_actions=1), repo=repo, bus=InvestigationBus()
    )
    incident = await _seed(orch, repo)

    await _record_decision(repo, incident, "rejected", "rejected")
    orch.start_resume(incident)
    await orch.wait_for(incident, timeout=5)

    rec = await repo.get(incident)
    assert rec is not None
    assert rec.status == COMPLETED
    assert InvestigationState.model_validate(rec.state_json).remediation_outcome == "rejected"


@pytest.mark.asyncio
async def test_empty_plan_interrupt_auto_resumes_to_completed() -> None:
    """An interrupt with no actions has nothing to approve — the run must drive
    straight through to completion rather than parking forever."""
    repo = InMemoryInvestigationRepository()
    orch = InvestigationOrchestrator(
        compiled_graph=_build_interrupt_graph(plan_actions=0), repo=repo, bus=InvestigationBus()
    )
    incident = await _seed(orch, repo)

    rec = await repo.get(incident)
    assert rec is not None
    assert rec.status == COMPLETED
    assert InvestigationState.model_validate(rec.state_json).remediation_outcome == "closed"
