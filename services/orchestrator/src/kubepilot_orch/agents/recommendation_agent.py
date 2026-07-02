"""Recommendation agent — enriches RCA's text recommendations into structured commands.

Single LLM call, no tool loop. Takes the RCAReport and investigation context;
returns a list of Recommendation objects ordered by priority.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError

from kubepilot_orch.agents.prompts import load_prompt
from kubepilot_orch.llm.base import Message, Role
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.state import InvestigationState, Recommendation

log = structlog.get_logger(__name__)

AGENT_NAME = "recommendation"


class _RecommendationList(BaseModel):
    """LLMs reliably produce {"recommendations": [...]} when given an object schema.

    Returning a bare list often produces inconsistent shapes across providers,
    so we wrap in a single-field object and unwrap on the way out.
    """

    recommendations: list[Recommendation] = Field(default_factory=list, max_length=4)


async def run(state: InvestigationState, *, llm: LLMRouter) -> list[Recommendation]:
    if state.rca is None:
        log.warning("recommendation_no_rca", incident=str(state.incident_id))
        return []

    user_msg = _build_user_message(state)
    resp = await llm.chat(
        role=Role.ANALYSIS,
        messages=[
            Message(role="system", content=load_prompt("recommendation_agent")),
            Message(role="user", content=user_msg),
        ],
        response_schema=_RecommendationList,
        temperature=0.0,
    )

    try:
        parsed = _RecommendationList.model_validate_json(resp.content)
    except (ValidationError, ValueError) as e:
        log.error("recommendation_invalid_output", error=str(e), content=resp.content[:500])
        return _fallback_from_rca(state)

    recs = parsed.recommendations[:4]
    # Defensive: any write-shaped command must require approval, even if the LLM said otherwise.
    for r in recs:
        if _has_write_command(r) and not r.requires_approval:
            r.requires_approval = True
    # Stable ordering by priority for downstream consumers.
    recs.sort(key=lambda r: (r.priority, r.title))
    return recs


def _build_user_message(state: InvestigationState) -> str:
    rca = state.rca
    assert rca is not None  # narrowed by caller

    parts = [
        f"Investigation query: {state.query}",
        f"Namespace: {state.namespace}",
        f"Service: {state.service or 'unspecified'}",
        "",
        "RCA report:",
        f"  root_cause: {rca.root_cause}",
        f"  root_cause_category: {rca.root_cause_category}",
        f"  confidence: {rca.confidence:.2f}",
        f"  reasoning: {rca.reasoning}",
        "",
        "RCA's text-only recommendations (your job is to enrich these into concrete commands):",
    ]
    for i, rec in enumerate(rca.recommendations or []):
        parts.append(f"  {i + 1}. {rec}")

    parts += [
        "",
        "Produce the structured Recommendation array now.",
        "Substitute real namespace + service names — no <PLACEHOLDERS>.",
    ]
    return "\n".join(parts)


def _has_write_command(rec: Recommendation) -> bool:
    """Heuristic: any kubectl/helm command that mutates state requires approval."""
    write_verbs = (
        " apply ",
        " delete ",
        " create ",
        " patch ",
        " replace ",
        " scale ",
        " rollout ",
        " edit ",
        " drain ",
        " cordon ",
        " uncordon ",
        " evict ",
        " rollback",
        " upgrade ",
    )
    for cmd in rec.commands:
        # Pad with spaces so we match verbs in context.
        padded = " " + cmd.lower() + " "
        if any(verb in padded for verb in write_verbs):
            return True
    return False


def _fallback_from_rca(state: InvestigationState) -> list[Recommendation]:
    """If the LLM produces unusable output, surface the raw RCA text as low-priority recs.

    The investigator still gets *something* actionable; the UI can flag these as
    'unstructured' so operators know the agent didn't finish enriching them.
    """
    assert state.rca is not None
    fallback: list[Recommendation] = []
    for i, txt in enumerate(state.rca.recommendations[:4]):
        fallback.append(
            Recommendation(
                title=txt[:80],
                rationale="Enrichment failed; surfaced from RCA verbatim. Manual interpretation required.",
                commands=[],
                risk="medium",
                reversibility="reversible",
                priority=i + 1,
                requires_approval=True,
            )
        )
    return fallback


def to_state_update(recs: list[Recommendation]) -> dict[str, Any]:
    """Partial state update produced by the recommendation node."""
    return {
        "recommendations": recs,
        "current_step": "recommendations_completed",
        "completed_agents": [AGENT_NAME],
    }
