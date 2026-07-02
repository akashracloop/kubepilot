"""Recommendation agent — enriches RCA's text recommendations into structured commands.

Single LLM call, no tool loop. Takes the RCAReport and investigation context;
returns a list of Recommendation objects ordered by priority.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError

from kubepilot_orch.agents.prompt_registry import resolve_prompt
from kubepilot_orch.guardrails import enforce
from kubepilot_orch.llm.base import Message, Role
from kubepilot_orch.llm.parsing import strip_code_fences
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.state import InvestigationState, Recommendation

log = structlog.get_logger(__name__)

AGENT_NAME = "recommendation"
PROMPT_NAME = "recommendation_agent"


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
    _, system_prompt = resolve_prompt(PROMPT_NAME, key=str(state.incident_id))
    resp = await llm.chat(
        role=Role.ANALYSIS,
        messages=[
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_msg),
        ],
        response_schema=_RecommendationList,
        temperature=0.0,
    )

    text = strip_code_fences(resp.content)
    try:
        recs = _RecommendationList.model_validate_json(text).recommendations
    except (ValidationError, ValueError):
        # Some models (e.g. gpt-4o-mini) return a bare JSON array instead of the
        # {"recommendations": [...]} object. Accept either shape.
        try:
            raw = json.loads(text)
            items = raw if isinstance(raw, list) else raw.get("recommendations", [])
            recs = [Recommendation.model_validate(item) for item in items]
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            log.error("recommendation_invalid_output", error=str(e), content=text[:500])
            return _fallback_from_rca(state)

    recs = recs[:4]
    # Guardrail (W10): drop destructive/forbidden recommendations and force approval
    # on any remaining write command. Blocked suggestions are logged for AgentOps.
    result = enforce(recs)
    if result.blocked_any:
        log.warning(
            "recommendations_guardrail",
            incident=str(state.incident_id),
            violations=[{"kind": v.kind, "title": v.title} for v in result.violations],
        )
    recs = result.kept
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

    # Phase 3: the critic's unresolved concerns should shape the recommendations —
    # e.g. add a verification step for an alternative cause it flagged.
    if state.critique is not None and state.critique.concerns:
        parts += ["", "Critic's concerns to address (weigh these when prioritizing):"]
        parts += [f"  - {c}" for c in state.critique.concerns]
        if state.critique.escalate_to_human:
            parts.append(
                "  NOTE: the critic flagged this finding for human review — prefer "
                "diagnostic/verification steps over aggressive remediation."
            )

    parts += [
        "",
        "Produce the structured Recommendation array now.",
        "Substitute real namespace + service names — no <PLACEHOLDERS>.",
    ]
    return "\n".join(parts)


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
