"""Critic agent — adversarially reviews the RCA before recommendation/finalize (Phase 3).

Like the RCA agent, the critic calls no MCP tools. It reasons over the RCAReport
plus the same evidence the RCA saw and produces a single structured ``Critique``:
an *independent* agreement score, concrete concerns, an adjusted confidence, and
an escalation flag. The graph uses it to (a) surface a critic-adjusted confidence
as ``calibrated_confidence`` (an interim until W7's empirical calibrator), (b) flag
low-agreement findings for a human, and (c) feed concerns into the recommendation.

Following the codebase contract, the provider does not validate structured output —
this caller validates and owns the fallback. On unparseable output the critic
*fails open*: it returns a neutral critique that neither corrupts nor rubber-stamps
the RCA, mirroring how rca_agent / recommendation_agent degrade.
"""

from __future__ import annotations

import structlog
from pydantic import ValidationError

from kubepilot_orch.agents.prompts import load_prompt
from kubepilot_orch.llm.base import Message, Role
from kubepilot_orch.llm.parsing import strip_code_fences
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.state import Critique, Evidence, InvestigationState

log = structlog.get_logger(__name__)

AGENT_NAME = "critic"

# Deterministic escalation thresholds applied on top of the model's own judgement.
# Low agreement OR a low adjusted confidence routes the finding to a human — the
# critic LLM can also set escalate_to_human itself; we OR the two so a slip in
# either direction still escalates.
ESCALATE_AGREEMENT_BELOW = 0.5
ESCALATE_CONFIDENCE_BELOW = 0.4


async def run(state: InvestigationState, *, llm: LLMRouter) -> Critique:
    """Produce a Critique of the current RCAReport.

    Single LLM call with response_schema=Critique. Falls back to a neutral,
    non-escalating critique if there is no RCA to review or the model produces
    unparseable output.
    """
    if state.rca is None:
        log.warning("critic_no_rca", incident=str(state.incident_id))
        return Critique(
            agreement=1.0,
            concerns=["No RCA report was available to critique."],
            adjusted_confidence=None,
            escalate_to_human=False,
        )

    user_msg = _build_user_message(state)
    resp = await llm.chat(
        role=Role.CRITIQUE,
        messages=[
            Message(role="system", content=load_prompt("critic_agent")),
            Message(role="user", content=user_msg),
        ],
        response_schema=Critique,
        temperature=0.0,
    )

    try:
        critique = Critique.model_validate_json(strip_code_fences(resp.content))
    except (ValidationError, ValueError) as e:
        log.error("critic_output_invalid", error=str(e), content=resp.content[:500])
        # Fail open: don't lower confidence on our own failure, but leave a note.
        return Critique(
            agreement=1.0,
            concerns=["Critic failed to produce a valid critique; RCA left unchanged."],
            adjusted_confidence=None,
            escalate_to_human=False,
        )

    return _apply_policy(critique, state)


def _apply_policy(critique: Critique, state: InvestigationState) -> Critique:
    """Post-process the model's critique with deterministic guarantees.

    - Derive ``adjusted_confidence`` from agreement when the model omitted it, so a
      low-agreement critique always tempers the RCA's raw confidence.
    - Force escalation when agreement or adjusted confidence falls below threshold,
      regardless of the flag the model set.
    """
    assert state.rca is not None  # narrowed by caller

    adjusted = critique.adjusted_confidence
    if adjusted is None:
        # No model-supplied number: temper the RCA's own confidence by agreement.
        adjusted = round(state.rca.confidence * critique.agreement, 2)
    adjusted = max(0.0, min(1.0, adjusted))

    escalate = (
        critique.escalate_to_human
        or critique.agreement < ESCALATE_AGREEMENT_BELOW
        or adjusted < ESCALATE_CONFIDENCE_BELOW
    )

    return critique.model_copy(
        update={"adjusted_confidence": adjusted, "escalate_to_human": escalate}
    )


def _build_user_message(state: InvestigationState) -> str:
    rca = state.rca
    assert rca is not None  # narrowed by caller

    parts = [
        f"Investigation query: {state.query}",
        f"Namespace: {state.namespace}",
        f"Service: {state.service or 'unspecified'}",
        "",
        "RCA report under review:",
        f"  root_cause: {rca.root_cause}",
        f"  root_cause_category: {rca.root_cause_category}",
        f"  stated_confidence: {rca.confidence:.2f}",
        f"  reasoning: {rca.reasoning}",
        f"  evidence_refs (indices it cited): {rca.evidence_refs}",
        "",
        "Evidence the RCA had available (cite indices in your concerns as needed):",
    ]
    if not state.evidence:
        parts.append("  (no evidence was collected — a confident root cause is suspect)")
    else:
        for i, ev in enumerate(state.evidence):
            parts.append(_format_evidence(i, ev))

    parts += [
        "",
        "Now refute or corroborate this RCA. Produce the structured Critique.",
    ]
    return "\n".join(parts)


def _format_evidence(idx: int, ev: Evidence) -> str:
    detail = ", ".join(f"{k}={v!r}" for k, v in (ev.detail or {}).items() if v is not None)
    if len(detail) > 240:
        detail = detail[:240] + "...[truncated]"
    return f"  [{idx}] ({ev.source_agent}/{ev.kind}, severity={ev.severity}) {ev.summary}" + (
        f"  | {detail}" if detail else ""
    )


def to_state_update(critique: Critique) -> dict:
    """Partial state update produced by the critic node.

    Singleton fields (no reducer) — the critic is the only node writing them in
    this serial position between RCA and recommendation. ``calibrated_confidence``
    is seeded here from the critic's adjusted confidence; W7 refines it against
    empirical eval history.
    """
    return {
        "critique": critique,
        "calibrated_confidence": critique.adjusted_confidence,
        "current_step": "critique_completed",
        "completed_agents": [AGENT_NAME],
    }
