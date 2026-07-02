"""Runtime-specific RCA libraries — selection + injection into the RCA prompt (W6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from kubepilot_orch.agents import rca_agent
from kubepilot_orch.rca.runtimes import (
    available_runtimes,
    detect_runtime,
    load_runtime_library,
    normalize_runtime,
    runtime_context,
)
from kubepilot_orch.state import AgentOutput, Evidence, InvestigationState, RCAReport, Severity
from kubepilot_orch.testing import ScriptedLLM, build_router, llm_text


def _state(evidence: list[Evidence]) -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.uuid4(),
        query="why is the service failing?",
        namespace="prod",
        service="svc",
        evidence=evidence,
        agent_outputs={"logs": AgentOutput(agent_name="logs", succeeded=True)},
        completed_agents=["logs", "rca"],
        started_at=datetime(2026, 7, 2, 10, 7, tzinfo=UTC),
    )


def _ev(runtime: str | None) -> Evidence:
    detail = {"runtime": runtime} if runtime is not None else {}
    return Evidence(
        source_agent="logs",
        kind="exception",
        summary="stack traces",
        detail=detail,
        severity=Severity.CRITICAL,
        collected_at=datetime(2026, 7, 2, 10, 8, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("java", "java"),
        ("JVM", "java"),
        ("Kotlin", "java"),
        ("golang", "go"),
        ("go", "go"),
        ("nodejs", "node"),
        ("Node.js", "node"),
        ("cpython", "python"),
        ("generic", None),
        ("dotnet", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_runtime(raw: str | None, expected: str | None) -> None:
    assert normalize_runtime(raw) == expected


def test_available_runtimes() -> None:
    assert available_runtimes() == ["go", "java", "node", "python"]


def test_detect_runtime_from_first_tagged_evidence() -> None:
    state = _state([_ev(None), _ev("java")])
    assert detect_runtime(state) == "java"


def test_detect_runtime_none_when_generic_or_absent() -> None:
    assert detect_runtime(_state([_ev("generic")])) is None
    assert detect_runtime(_state([_ev(None)])) is None


def test_load_runtime_library_content_and_miss() -> None:
    java = load_runtime_library("java")
    assert java is not None and "JVM" in java
    go = load_runtime_library("go")
    assert go is not None and "goroutine" in go.lower()
    assert load_runtime_library("cobol") is None


def test_runtime_context_pairs_runtime_and_text() -> None:
    runtime, text = runtime_context(_state([_ev("go")]))
    assert runtime == "go"
    assert text is not None and "goroutine" in text.lower()
    # No recognized runtime → (None, None), RCA stays runtime-agnostic.
    assert runtime_context(_state([_ev("generic")])) == (None, None)


@pytest.mark.asyncio
async def test_rca_injects_matching_runtime_library_java_vs_go() -> None:
    """The Go incident gets goroutine guidance; the Java incident gets JVM guidance."""
    report = RCAReport(
        root_cause="x",
        root_cause_category="OOMKilled",
        confidence=0.9,
        evidence_refs=[0],
        reasoning="y",
        recommendations=["z"],
    )

    # Go-tagged incident.
    go_llm = ScriptedLLM(responses=[llm_text(report.model_dump_json())])
    await rca_agent.run(_state([_ev("go")]), llm=build_router(go_llm))
    go_prompt = next(m for m in go_llm.calls[0]["messages"] if m.role == "user").content
    assert "Runtime-specific reasoning" in go_prompt
    assert "runtime=go" in go_prompt
    assert "pprof" in go_prompt  # go-specific guidance present
    assert "Metaspace" not in go_prompt  # java-only marker must not leak in

    # Java-tagged incident.
    java_llm = ScriptedLLM(responses=[llm_text(report.model_dump_json())])
    await rca_agent.run(_state([_ev("java")]), llm=build_router(java_llm))
    java_prompt = next(m for m in java_llm.calls[0]["messages"] if m.role == "user").content
    assert "runtime=java" in java_prompt
    assert "Metaspace" in java_prompt  # java-specific guidance present
    assert "pprof" not in java_prompt  # go-only marker must not leak in


@pytest.mark.asyncio
async def test_rca_omits_runtime_section_when_generic() -> None:
    report = RCAReport(root_cause="x", root_cause_category="Unknown", confidence=0.5, reasoning="y")
    llm = ScriptedLLM(responses=[llm_text(report.model_dump_json())])
    await rca_agent.run(_state([_ev("generic")]), llm=build_router(llm))
    prompt = next(m for m in llm.calls[0]["messages"] if m.role == "user").content
    assert "Runtime-specific reasoning" not in prompt
