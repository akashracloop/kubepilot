"""Remediation agent — turns the RCA + recommendations into an executable plan (Phase 4).

Produces a ranked ``RemediationPlan`` of ``RemediationAction``s, each mapped to a
tool in the curated write catalog. **It never executes** — execution is a separate,
gated step (policy → blast radius → HITL approval → executor). Reversibility and
approval tier are code-filled from the catalog (never trusted from the model), and
any action referencing a tool outside the catalog is dropped, so nothing
destructive can ever reach a plan.

Fail-safe: unparseable model output yields an **empty plan** (no action), never a
guessed one — the read-only default is always safe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError

from kubepilot_orch.agents.prompt_registry import resolve_prompt
from kubepilot_orch.llm.base import Message, Role
from kubepilot_orch.llm.parsing import clean_json
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.remediation.catalog import WRITE_CATALOG, catalog_prompt
from kubepilot_orch.state import InvestigationState, RemediationAction, RemediationPlan

log = structlog.get_logger(__name__)

AGENT_NAME = "remediation"
PROMPT_NAME = "remediation_agent"


class _PlanAction(BaseModel):
    tool: str
    target: str
    namespace: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    priority: int = 1


class _PlanOutput(BaseModel):
    actions: list[_PlanAction] = Field(default_factory=list, max_length=4)


async def run(state: InvestigationState, *, llm: LLMRouter) -> RemediationPlan:
    """Propose a remediation plan for the concluded incident (never executes)."""
    if state.rca is None:
        return RemediationPlan(actions=[], notes="no RCA — nothing to remediate")

    _, system = resolve_prompt(PROMPT_NAME, key=str(state.incident_id))
    system = system.replace("{catalog}", catalog_prompt())

    resp = await llm.chat(
        role=Role.ANALYSIS,
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=_build_user_message(state)),
        ],
        response_schema=_PlanOutput,
        temperature=0.0,
    )

    try:
        proposed = _PlanOutput.model_validate_json(clean_json(resp.content)).actions
    except (ValidationError, ValueError) as e:
        log.error("remediation_plan_invalid", error=str(e), content=resp.content[:400])
        return RemediationPlan(actions=[], notes="remediation planning failed — no action proposed")

    actions = _to_actions(proposed)
    return RemediationPlan(
        actions=actions,
        notes=None if actions else "no safe catalog action addresses this root cause",
        generated_at=datetime.now(UTC),
    )


def _to_actions(proposed: list[_PlanAction]) -> list[RemediationAction]:
    """Map proposals to RemediationActions, dropping anything outside the catalog and
    code-filling reversibility + approval tier (never trusting the model)."""
    out: list[RemediationAction] = []
    for p in proposed:
        spec = WRITE_CATALOG.get(p.tool)
        if spec is None:
            log.warning("remediation_action_dropped", tool=p.tool, reason="not in write catalog")
            continue
        out.append(
            RemediationAction(
                tool=spec.name,
                target=p.target,
                namespace=p.namespace,
                arguments=p.arguments,
                rationale=p.rationale,
                reversibility=spec.reversibility,  # authoritative, from the catalog
                approval_tier=spec.approval_tier,
                priority=p.priority,
            )
        )
    out.sort(key=lambda a: a.priority)
    return out[:4]


def _build_user_message(state: InvestigationState) -> str:
    rca = state.rca
    assert rca is not None
    parts = [
        f"Investigation query: {state.query}",
        f"Namespace: {state.namespace}",
        f"Service: {state.service or 'unspecified'}",
        "",
        "RCA:",
        f"  root_cause: {rca.root_cause}",
        f"  category: {rca.root_cause_category}",
        f"  confidence: {rca.confidence:.2f}",
        "",
        "Text recommendations from the recommendation agent:",
    ]
    parts += [f"  - {r.title}: {r.rationale}" for r in state.recommendations] or ["  (none)"]
    if state.knowledge_context:
        parts.append("")
        parts.append("Ownership/dependencies (for targeting):")
        for k in state.knowledge_context:
            parts.append(f"  - {k.service} owned by {k.owner or 'unknown'}, deps: {k.dependencies}")
    parts += ["", "Produce the JSON remediation plan now (reversible-first, or empty)."]
    return "\n".join(parts)


def to_state_update(plan: RemediationPlan) -> dict[str, Any]:
    """Partial state update produced by the remediation node.

    Sets the plan and marks the incident ``pending_approval`` when there are
    actions (the HITL gate, W5, resolves it); no actions → no remediation outcome.
    """
    outcome = "pending_approval" if plan.actions else None
    return {
        "remediation_plan": plan,
        "remediation_outcome": outcome,
        "current_step": "remediation_planned",
        "completed_agents": [AGENT_NAME],
    }
