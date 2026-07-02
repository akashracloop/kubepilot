"""Deterministic CI self-test for the eval HARNESS — not model accuracy.

Runnable with:  ``uv run pytest eval -p no:cacheprovider``

These tests validate that the harness plumbing is correct:
  - the §7.2 SCORER math (category + confidence + evidence → score) is right, and
  - the RUNNER wires a scenario's fixture through an ``httpx.MockTransport`` MCP
    client into the real investigation graph and yields an ``RCAReport``.

They use hand-built inputs and a ``ScriptedLLM`` dispatcher (same pattern as
``services/orchestrator/tests/test_end_to_end_investigation.py``); no real LLM is
called. Measuring actual RCA accuracy against a live model is the job of
``run_eval.py`` (``make eval``), which needs an API key and is out of scope here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from kubepilot_orch.agents.recommendation_agent import _RecommendationList
from kubepilot_orch.state import (
    AgentOutput,
    Evidence,
    RCAReport,
    Recommendation,
    Severity,
)
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_router,
    llm_text,
    llm_tool_call,
)

from eval.harness.loader import Expected, Scenario, load_scenarios
from eval.harness.runner import run_scenario
from eval.harness.scorer import aggregate, score_scenario

# ===========================================================================
# Dataset sanity — the golden set must load and cover the §7.1 baseline size.
# ===========================================================================


def test_dataset_loads_and_is_large_enough() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) >= 20, "PHASE_1_PLAN §7.1 requires ≥20 golden scenarios"
    # Every scenario must be gradeable: a category and at least one server fixture.
    for s in scenarios:
        assert s.expected.root_cause_category
        assert s.fixture, f"{s.id} has no MCP fixture"


# ===========================================================================
# SCORER math — hand-built RCAReports, no graph involved.
# ===========================================================================


def _scenario(**expected: Any) -> Scenario:
    return Scenario(
        id="unit-scenario",
        query="why is svc failing?",
        namespace="prod",
        service="svc",
        fixture={"mcp-k8s": {"list_pods": []}},
        expected=Expected(**expected),
    )


def _evidence(summary: str, **detail: Any) -> Evidence:
    return Evidence(
        source_agent="kubernetes",
        kind="pod_state",
        summary=summary,
        detail=detail,
        severity=Severity.CRITICAL,
        collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
    )


def test_scorer_perfect_match_scores_one() -> None:
    scenario = _scenario(
        root_cause_category="OOMKilled",
        min_confidence=0.7,
        must_mention_evidence=["memory", "restart", "137"],
    )
    rca = RCAReport(
        root_cause="JVM heap exhaustion: container OOMKilled with last exit code 137.",
        root_cause_category="OOMKilled",
        confidence=0.92,
        evidence_refs=[0],
        reasoning="Memory grew until the kernel OOM-killer fired; 12 restarts observed.",
        recommendations=["Increase memory limit", "Roll back the deploy"],
    )
    evidence = [_evidence("payment-service-0 OOMKilled, 12 restart events", last_exit_code=137)]

    b = score_scenario(scenario, rca, evidence)
    assert b.category_match is True
    assert b.confidence_ok is True
    assert b.evidence_ok is True
    assert b.evidence_hit_count == 3
    assert b.score == pytest.approx(1.0)


def test_scorer_wrong_category_low_confidence_missing_evidence() -> None:
    scenario = _scenario(
        root_cause_category="OOMKilled",
        min_confidence=0.7,
        must_mention_evidence=["memory", "restart", "137"],
    )
    # Wrong category, confidence well below (0.5 < 0.7 - 0.05), and the RCA text
    # mentions none of the required evidence substrings.
    rca = RCAReport(
        root_cause="Image could not be pulled.",
        root_cause_category="ImagePullBackOff",
        confidence=0.50,
        evidence_refs=[],
        reasoning="Registry returned 401.",
        recommendations=["Fix the pull secret"],
    )

    b = score_scenario(scenario, rca, evidence=[])
    assert b.category_match is False
    assert b.confidence_ok is False
    assert b.evidence_ok is False
    assert b.evidence_hit_count == 0
    assert b.score == pytest.approx(0.0)


def test_scorer_partial_credit_and_confidence_tolerance() -> None:
    scenario = _scenario(
        root_cause_category="OOMKilled",
        min_confidence=0.7,
        must_mention_evidence=["memory", "137"],
    )
    # Correct category; confidence 0.66 is inside the 0.05 tolerance band (>= 0.65);
    # only one of two evidence terms present → evidence component fails.
    rca = RCAReport(
        root_cause="Out-of-memory kill suspected.",
        root_cause_category="OOMKilled",
        confidence=0.66,
        evidence_refs=[],
        reasoning="High memory usage before the crash.",
        recommendations=[],
    )

    b = score_scenario(scenario, rca, evidence=[])
    assert b.category_match is True
    assert b.confidence_ok is True  # within tolerance
    assert b.evidence_ok is False  # "137" missing
    assert b.evidence_hit_count == 1
    assert b.score == pytest.approx(2.0 / 3.0)


def test_scorer_none_rca_scores_zero() -> None:
    scenario = _scenario(
        root_cause_category="OOMKilled", min_confidence=0.7, must_mention_evidence=["memory"]
    )
    b = score_scenario(scenario, None, evidence=[])
    assert b.score == pytest.approx(0.0)
    assert b.actual_category is None
    assert b.actual_confidence is None


def test_aggregate_mean_and_gate() -> None:
    scenario = _scenario(
        root_cause_category="OOMKilled", min_confidence=0.7, must_mention_evidence=["memory"]
    )
    good = RCAReport(
        root_cause="memory exhaustion",
        root_cause_category="OOMKilled",
        confidence=0.9,
        reasoning="memory grew",
        recommendations=[],
    )
    bad = RCAReport(
        root_cause="unknown",
        root_cause_category="Unknown",
        confidence=0.1,
        reasoning="no signal",
        recommendations=[],
    )
    breakdowns = [
        score_scenario(scenario, good, []),  # 1.0
        score_scenario(scenario, bad, []),  # 0.0
    ]
    agg = aggregate(breakdowns)
    assert agg.count == 2
    assert agg.mean_score == pytest.approx(0.5)
    assert agg.perfect_count == 1
    assert agg.passes_gate is False  # 0.5 < 0.70


# ===========================================================================
# RUNNER plumbing — real graph, mocked MCP transport, ScriptedLLM.
# ===========================================================================


def _agent_output(agent: str, summary: str, **detail: Any) -> AgentOutput:
    return AgentOutput(
        agent_name=agent,
        succeeded=True,
        evidence=[
            Evidence(
                source_agent=agent,
                kind="observation",
                summary=summary,
                detail=detail,
                severity=Severity.CRITICAL,
                collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
            )
        ],
    )


def _build_dispatcher(scenario: Scenario) -> Any:
    """Scripted-LLM dispatcher: one tool call per specialist, then RCA + recs.

    Each specialist calls the first tool staged for its server in the fixture,
    which exercises the MockTransport wiring end-to-end.
    """
    k8s_tool = next(iter(scenario.server_fixture("mcp-k8s")))
    prom_tool = next(iter(scenario.server_fixture("mcp-prom")))
    loki_tool = next(iter(scenario.server_fixture("mcp-loki")))

    k8s = ScriptedLLM(
        name="k8s",
        responses=[
            llm_tool_call(k8s_tool, {"namespace": scenario.namespace}, call_id="k1"),
            llm_text("collected pod state"),
            llm_text(
                _agent_output(
                    "kubernetes", "payment-service-0 OOMKilled, 12 restarts", last_exit_code=137
                ).model_dump_json()
            ),
        ],
    )
    metrics = ScriptedLLM(
        name="metrics",
        responses=[
            llm_tool_call(
                prom_tool, {"promql": "container_memory_working_set_bytes"}, call_id="m1"
            ),
            llm_text("collected metrics"),
            llm_text(
                _agent_output(
                    "metrics",
                    "memory grew 256MiB → 1024MiB before the crash",
                    peak_bytes=1073741824,
                ).model_dump_json()
            ),
        ],
    )
    logs = ScriptedLLM(
        name="logs",
        responses=[
            llm_tool_call(loki_tool, {"service": scenario.service}, call_id="l1"),
            llm_text("collected logs"),
            llm_text(
                _agent_output(
                    "logs", "23 java.lang.OutOfMemoryError stack traces", count=23
                ).model_dump_json()
            ),
        ],
    )
    rca = ScriptedLLM(
        name="rca",
        responses=[
            llm_text(
                RCAReport(
                    root_cause="JVM heap exhaustion: OOMKilled with exit code 137, 12 restarts.",
                    root_cause_category="OOMKilled",
                    confidence=0.9,
                    evidence_refs=[0],
                    reasoning="Memory saturation corroborated by OutOfMemoryError traces.",
                    recommendations=["Roll back the deploy", "Raise the memory limit"],
                ).model_dump_json()
            )
        ],
    )
    recommendation = ScriptedLLM(
        name="recommendation",
        responses=[
            llm_text(
                _RecommendationList(
                    recommendations=[
                        Recommendation(
                            title="Roll back deployment",
                            rationale="Restore the pre-regression image.",
                            commands=["kubectl rollout undo deployment/payment-service -n prod"],
                            risk="medium",
                            reversibility="reversible",
                            priority=1,
                            requires_approval=True,
                        )
                    ]
                ).model_dump_json()
            )
        ],
    )

    by_keyword = [
        ("Kubernetes specialist", k8s),
        ("metrics specialist", metrics),
        ("logs specialist", logs),
        ("Root-Cause Analysis", rca),
        ("Recommendation agent", recommendation),
    ]

    class Dispatcher:
        name = "dispatcher"

        async def chat(self, messages: Any, **kwargs: Any) -> Any:
            system = next((m.content for m in messages if m.role == "system"), "")
            for keyword, llm in by_keyword:
                if keyword in system:
                    return await llm.chat(messages, **kwargs)
            raise AssertionError(f"No scripted LLM matched system prompt: {system[:80]!r}")

    return Dispatcher()


@pytest.mark.asyncio
async def test_runner_wires_fixture_through_graph_to_rca() -> None:
    # Use a real golden scenario that stages all three MCP servers.
    scenario = next(s for s in load_scenarios() if s.id == "java-spring-oom-001")
    dispatcher = _build_dispatcher(scenario)
    router = build_router(dispatcher)

    state = await run_scenario(scenario, router)

    # The graph ran to completion and produced a structured RCA report.
    assert state.rca is not None
    assert isinstance(state.rca, RCAReport)
    assert state.rca.root_cause_category == "OOMKilled"
    assert state.current_step == "completed"

    # Evidence from all three specialists made it into merged state — proves the
    # MockTransport fixture was served to each specialist's tool call.
    sources = {e.source_agent for e in state.evidence}
    assert {"kubernetes", "metrics", "logs"}.issubset(sources)

    # Recommendation node produced a concrete, approval-gated command.
    assert state.recommendations
    assert any("kubectl" in cmd for r in state.recommendations for cmd in r.commands)

    # Runner output feeds the scorer cleanly (integration of both halves).
    breakdown = score_scenario(scenario, state.rca, state.evidence)
    assert breakdown.category_match is True
    assert breakdown.confidence_ok is True
    assert breakdown.score == pytest.approx(1.0)
