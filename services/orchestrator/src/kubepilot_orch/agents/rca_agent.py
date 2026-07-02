"""RCA agent — correlates evidence from specialist sub-agents into an RCAReport.

Unlike specialists, the RCA agent does not call MCP tools. It reasons over the
evidence already in state.evidence and produces a single structured report.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from pydantic import ValidationError

from kubepilot_orch.agents.prompts import load_prompt
from kubepilot_orch.llm.base import Message, Role
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.state import Evidence, InvestigationState, RCAReport

log = structlog.get_logger(__name__)

AGENT_NAME = "rca"


async def run(state: InvestigationState, *, llm: LLMRouter) -> RCAReport:
    """Produce a root-cause report from the evidence collected by specialist agents.

    Single LLM call with response_schema=RCAReport. Falls back to a low-confidence
    "Unknown" report if the model produces unparseable output.
    """
    user_msg = _build_user_message(state)

    resp = await llm.chat(
        role=Role.ANALYSIS,
        messages=[
            Message(role="system", content=load_prompt("rca_agent")),
            Message(role="user", content=user_msg),
        ],
        response_schema=RCAReport,
        temperature=0.0,
    )

    try:
        report = RCAReport.model_validate_json(resp.content)
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

    parts.append("")
    parts.append("Produce the structured RCAReport now.")
    return "\n".join(parts)


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
    in this serial position of the graph.
    """
    return {
        "rca": report,
        "confidence": report.confidence,
        "current_step": "rca_completed",
        "completed_agents": [AGENT_NAME],
        "finished_at": datetime.now(UTC),
    }
