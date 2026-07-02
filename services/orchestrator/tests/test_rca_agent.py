"""RCA agent unit tests — given evidence, produce a structured RCAReport."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from kubepilot_orch.agents import rca_agent
from kubepilot_orch.state import (
    AgentOutput,
    Evidence,
    InvestigationState,
    RCAReport,
    Severity,
)
from kubepilot_orch.testing import ScriptedLLM, build_router, llm_text


def _state_with_evidence(evidence: list[Evidence]) -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query="why is payment-service failing?",
        namespace="prod",
        service="payment-service",
        evidence=evidence,
        agent_outputs={
            "kubernetes": AgentOutput(agent_name="kubernetes", succeeded=True),
            "metrics": AgentOutput(agent_name="metrics", succeeded=True),
            "logs": AgentOutput(agent_name="logs", succeeded=True),
        },
        completed_agents=["kubernetes", "metrics", "logs"],
        started_at=datetime(2026, 6, 23, 10, 7, tzinfo=UTC),
    )


_OOM_EVIDENCE = [
    Evidence(
        source_agent="kubernetes",
        kind="pod_state",
        summary="payment-service-0 in CrashLoopBackOff, last termination OOMKilled (137).",
        detail={"restart_count": 12, "last_exit_code": 137},
        severity=Severity.CRITICAL,
        collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
    ),
    Evidence(
        source_agent="metrics",
        kind="resource_saturation",
        summary="Memory grew 256MiB → 1024MiB in 15min on payment-service-0.",
        detail={"baseline_bytes": 268435456, "peak_bytes": 1073741824},
        severity=Severity.CRITICAL,
        collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
    ),
    Evidence(
        source_agent="logs",
        kind="exception_pattern",
        summary="23 java.lang.OutOfMemoryError stack traces (runtime=java).",
        detail={"runtime": "java", "count": 23, "exception_class": "java.lang.OutOfMemoryError"},
        severity=Severity.CRITICAL,
        collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
    ),
]


@pytest.mark.asyncio
async def test_rca_correlates_three_signals_into_high_confidence_oom() -> None:
    expected_report = RCAReport(
        root_cause="JVM heap exhaustion in payment-service: OOMKilled corroborated by memory saturation and java.lang.OutOfMemoryError traces.",
        root_cause_category="OOMKilled",
        confidence=0.92,
        evidence_refs=[0, 1, 2],
        reasoning=(
            "Three specialists corroborate: K8s shows OOMKilled exit_code=137 with 12 restarts; "
            "Metrics show memory growing 4x to 1024MiB in 15 minutes; Logs show 23 java.lang.OutOfMemoryError "
            "stack traces. The mechanism (Java heap exhausted) and the symptom (OOMKilled) align."
        ),
        recommendations=[
            "Roll back payment-service to the previous version",
            "Increase memory limit to 2Gi as short-term mitigation",
            "Investigate cache or allocation growth in the new code path",
            "Add a heap-usage alert at 75% of the new limit",
        ],
    )

    state = _state_with_evidence(_OOM_EVIDENCE)
    scripted = ScriptedLLM(responses=[llm_text(expected_report.model_dump_json())])

    report = await rca_agent.run(state, llm=build_router(scripted))

    assert report.root_cause_category == "OOMKilled"
    assert report.confidence >= 0.85
    assert set(report.evidence_refs) <= {0, 1, 2}
    assert len(report.recommendations) >= 1
    assert len(report.recommendations) <= 4


@pytest.mark.asyncio
async def test_rca_clamps_invalid_evidence_refs() -> None:
    """The LLM might cite out-of-range indices — RCA agent must filter them out."""
    report_with_bad_refs = RCAReport(
        root_cause="...",
        root_cause_category="OOMKilled",
        confidence=0.9,
        evidence_refs=[0, 1, 2, 99, -1],  # 99 and -1 are invalid; we have 3 items
        reasoning="...",
        recommendations=["..."],
    )
    state = _state_with_evidence(_OOM_EVIDENCE)
    scripted = ScriptedLLM(responses=[llm_text(report_with_bad_refs.model_dump_json())])

    report = await rca_agent.run(state, llm=build_router(scripted))
    assert report.evidence_refs == [0, 1, 2]


@pytest.mark.asyncio
async def test_rca_handles_sparse_evidence_with_low_confidence_unknown() -> None:
    """When evidence is sparse/contradictory, the report should be honest."""
    sparse = [
        Evidence(
            source_agent="kubernetes",
            kind="pod_state",
            summary="All pods Running.",
            detail={},
            severity=Severity.INFO,
            collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
        )
    ]

    expected = RCAReport(
        root_cause="Insufficient signals to determine a root cause.",
        root_cause_category="Unknown",
        confidence=0.2,
        evidence_refs=[],
        reasoning="Only K8s reported and it shows pods healthy; metrics and logs returned nothing actionable.",
        recommendations=["Re-run with a wider time window", "Manually inspect application logs"],
    )

    state = _state_with_evidence(sparse)
    scripted = ScriptedLLM(responses=[llm_text(expected.model_dump_json())])

    report = await rca_agent.run(state, llm=build_router(scripted))
    assert report.root_cause_category == "Unknown"
    assert report.confidence < 0.3


@pytest.mark.asyncio
async def test_rca_recovers_from_invalid_summary_json() -> None:
    """Garbage output from the LLM → low-confidence Unknown report, not a crash."""
    scripted = ScriptedLLM(responses=[llm_text("definitely not valid json")])
    state = _state_with_evidence(_OOM_EVIDENCE)

    report = await rca_agent.run(state, llm=build_router(scripted))
    assert report.root_cause_category == "Unknown"
    assert report.confidence == 0.0
    assert "failed" in report.root_cause.lower()


@pytest.mark.asyncio
async def test_rca_user_message_includes_numbered_evidence() -> None:
    """The prompt must present evidence with indices so the LLM can cite them."""
    state = _state_with_evidence(_OOM_EVIDENCE)
    expected = RCAReport(
        root_cause="...",
        root_cause_category="OOMKilled",
        confidence=0.9,
        evidence_refs=[0],
        reasoning="...",
        recommendations=["..."],
    )
    scripted = ScriptedLLM(responses=[llm_text(expected.model_dump_json())])

    await rca_agent.run(state, llm=build_router(scripted))

    # The single chat call's user message must list evidence with [N] indices.
    user_msg = next(m for m in scripted.calls[0]["messages"] if m.role == "user")
    assert "[0]" in user_msg.content
    assert "[1]" in user_msg.content
    assert "[2]" in user_msg.content
    assert "OOMKilled" in user_msg.content
