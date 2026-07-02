"""RCA agent — correlates evidence from specialist sub-agents into an RCAReport.

Unlike specialists, the RCA agent does not call MCP tools. It reasons over the
evidence already in state.evidence and produces a single structured report.
"""

from __future__ import annotations

import structlog
from pydantic import ValidationError

from kubepilot_orch.agents.prompt_registry import resolve_prompt
from kubepilot_orch.llm.base import Message, Role
from kubepilot_orch.llm.parsing import clean_json
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.rca.runtimes import runtime_context
from kubepilot_orch.state import Evidence, InvestigationState, RCAReport, ServiceKnowledge

log = structlog.get_logger(__name__)

AGENT_NAME = "rca"
PROMPT_NAME = "rca_agent"


async def run(state: InvestigationState, *, llm: LLMRouter) -> RCAReport:
    """Produce a root-cause report from the evidence collected by specialist agents.

    Single LLM call with response_schema=RCAReport. Falls back to a low-confidence
    "Unknown" report if the model produces unparseable output.
    """
    user_msg = _build_user_message(state)
    _, system_prompt = resolve_prompt(PROMPT_NAME, key=str(state.incident_id))

    resp = await llm.chat(
        role=Role.ANALYSIS,
        messages=[
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_msg),
        ],
        response_schema=RCAReport,
        temperature=0.0,
    )

    try:
        report = RCAReport.model_validate_json(clean_json(resp.content))
    except (ValidationError, ValueError) as e:
        log.error("rca_summary_invalid", error=str(e), content=resp.content[:500])
        report = RCAReport(
            root_cause="RCA failed to produce a valid structured report.",
            root_cause_category="Unknown",
            confidence=0.0,
            evidence_refs=[],
            reasoning=f"LLM output could not be validated against RCAReport schema: {e}",
            recommendations=["Re-run investigation", "Inspect agent traces in Phoenix/LangSmith"],
        )

    # Clamp evidence_refs to valid indices — defensive against LLM hallucination.
    n = len(state.evidence)
    report.evidence_refs = [i for i in report.evidence_refs if 0 <= i < n]

    return report


def _build_user_message(state: InvestigationState) -> str:
    parts = [
        f"Investigation query: {state.query}",
        f"Namespace: {state.namespace}",
        f"Service: {state.service or 'unspecified'}",
        "",
        "Specialist sub-agents that ran:",
    ]
    for name, output in sorted(state.agent_outputs.items()):
        status = "succeeded" if output.succeeded else "FAILED"
        parts.append(f"  - {name} ({status}, {len(output.evidence)} evidence items)")
        if output.notes:
            parts.append(f"      notes: {output.notes}")

    parts.append("")
    parts.append("Collected evidence (cite by index in evidence_refs):")
    if not state.evidence:
        parts.append("  (no evidence collected — explain why a root cause cannot be determined)")
    else:
        for i, ev in enumerate(state.evidence):
            parts.append(_format_evidence(i, ev))

    runtime, runtime_library = runtime_context(state)
    if runtime_library:
        parts.append("")
        parts.append(
            f"Runtime-specific reasoning (the Logs agent detected runtime={runtime}). Apply the "
            "patterns below ONLY where they match the evidence; they sharpen the category and the "
            "recommendation but must not override contradictory signals:"
        )
        parts.append(runtime_library.strip())

    if state.knowledge_context:
        parts.append("")
        parts.append(
            "Cluster knowledge (from the service graph — corroborating context, NOT evidence; "
            "do not cite in evidence_refs). Use it to name the owning team, weigh a dependency "
            "as a suspect, and check SLO breaches:"
        )
        for fact in state.knowledge_context:
            parts.append(_format_knowledge(fact))

    if state.memory_context:
        parts.append("")
        parts.append(
            "Similar past incidents (long-term memory — corroborating context, NOT evidence; "
            "weigh by similarity, do not cite in evidence_refs):"
        )
        for past in state.memory_context:
            outcome = f" → resolved: {past.outcome}" if past.outcome else ""
            parts.append(
                f"  - [{past.similarity:.2f}] {past.summary}"
                f" (category={past.root_cause_category or 'unknown'}){outcome}"
            )

    parts.append("")
    parts.append("Produce the structured RCAReport now.")
    return "\n".join(parts)


def _format_knowledge(fact: ServiceKnowledge) -> str:
    bits = [f"  - {fact.service}"]
    if fact.owner:
        bits.append(f"owned by {fact.owner}")
    if fact.dependencies:
        bits.append(f"depends on {', '.join(fact.dependencies)}")
    if fact.dependents:
        bits.append(f"depended on by {', '.join(fact.dependents)}")
    if fact.slos:
        slos = ", ".join(f"{k}={v}" for k, v in fact.slos.items())
        bits.append(f"SLOs: {slos}")
    if fact.last_deploy:
        bits.append(f"last deploy {fact.last_deploy}")
    return "; ".join(bits)


def _format_evidence(idx: int, ev: Evidence) -> str:
    detail = ", ".join(f"{k}={v!r}" for k, v in (ev.detail or {}).items() if v is not None)
    if len(detail) > 240:
        detail = detail[:240] + "...[truncated]"
    return f"  [{idx}] ({ev.source_agent}/{ev.kind}, severity={ev.severity}) {ev.summary}" + (
        f"  | {detail}" if detail else ""
    )


def to_state_update(report: RCAReport) -> dict:
    """Partial state update produced by the RCA node.

    Singleton fields (no reducer needed) — RCA is the only node writing them
    in this serial position of the graph. ``finished_at`` is deliberately NOT
    set here: recommendation + finalize still run after RCA, so the terminal
    timestamp is owned exclusively by ``finalize_node``.
    """
    return {
        "rca": report,
        "confidence": report.confidence,
        "current_step": "rca_completed",
        "completed_agents": [AGENT_NAME],
    }
