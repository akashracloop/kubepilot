"""W6 acceptance test.

Spec from PHASE_1_PLAN.md W6:
    "End-to-end LangGraph run on a fixture incident produces RCA report"

Runs the full Phase-1 pipeline:
    START → supervisor → (K8s ∥ Metrics ∥ Logs) → RCA → finalize → END

with scripted LLMs and mocked MCP servers, and asserts the final state contains:
  - merged evidence from all three specialists
  - an RCAReport with the expected root cause + confidence
  - terminal stamps (current_step="completed", finished_at set, confidence on state)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from kubepilot_orch.agents import kubernetes_agent, logs_agent, metrics_agent, rca_agent
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.state import AgentOutput, Evidence, RCAReport, Severity
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)

# ----------------------------------------------------------------------------
# Mocked tool surfaces — same OOM scenario as the W5 test
# ----------------------------------------------------------------------------

_K8S_TOOL_DESCRIPTORS = {
    "tools": [
        {
            "name": "list_pods",
            "description": "List pods.",
            "parameters": {
                "type": "object",
                "properties": {"namespace": {"type": "string"}},
                "required": ["namespace"],
            },
        }
    ]
}
_PROM_TOOL_DESCRIPTORS = {
    "tools": [
        {
            "name": "query_range",
            "description": "Range query.",
            "parameters": {
                "type": "object",
                "properties": {"promql": {"type": "string"}},
                "required": ["promql"],
            },
        }
    ]
}
_LOKI_TOOL_DESCRIPTORS = {
    "tools": [
        {
            "name": "search_exceptions",
            "description": "Workload-agnostic exception detection.",
            "parameters": {
                "type": "object",
                "properties": {"namespace": {"type": "string"}},
                "required": ["namespace"],
            },
        }
    ]
}


def _k8s_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/mcp/tools":
        return httpx.Response(200, json=_K8S_TOOL_DESCRIPTORS)
    body = json.loads(request.content.decode())
    return httpx.Response(
        200,
        json={
            "tool": body["tool"],
            "result": [
                {
                    "name": "payment-service-0",
                    "namespace": "prod",
                    "phase": "Running",
                    "status_reason": "CrashLoopBackOff",
                    "restart_count": 12,
                    "containers": [
                        {
                            "name": "app",
                            "image": "payment-service:v1.24.8",
                            "ready": False,
                            "restart_count": 12,
                            "state": "waiting",
                            "state_reason": "CrashLoopBackOff",
                            "exit_code": None,
                            "last_termination_reason": "OOMKilled",
                            "last_exit_code": 137,
                        }
                    ],
                    "labels": {"app": "payment-service"},
                }
            ],
        },
    )


def _prom_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/mcp/tools":
        return httpx.Response(200, json=_PROM_TOOL_DESCRIPTORS)
    body = json.loads(request.content.decode())
    return httpx.Response(
        200,
        json={
            "tool": body["tool"],
            "result": {
                "query": "container_memory_working_set_bytes",
                "result_type": "matrix",
                "series": [
                    {
                        "labels": {"pod": "payment-service-0"},
                        "samples": [
                            {"timestamp": "2026-06-23T09:50:00Z", "value": 268435456.0},
                            {"timestamp": "2026-06-23T10:05:00Z", "value": 1073741824.0},
                        ],
                    }
                ],
            },
        },
    )


def _loki_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/mcp/tools":
        return httpx.Response(200, json=_LOKI_TOOL_DESCRIPTORS)
    body = json.loads(request.content.decode())
    return httpx.Response(
        200,
        json={
            "tool": body["tool"],
            "result": {
                "query": "...",
                "total": 23,
                "by_runtime": {"java": 23},
                "matches": [
                    {
                        "timestamp": "2026-06-23T10:05:00Z",
                        "line": "java.lang.OutOfMemoryError: Java heap space",
                        "runtime": "java",
                        "exception_class": "java.lang.OutOfMemoryError",
                        "stream_labels": {"app": "payment-service"},
                    }
                ],
            },
        },
    )


# ----------------------------------------------------------------------------
# Scripted-LLM dispatcher — multiplexes by which agent's system prompt is in play
# ----------------------------------------------------------------------------


def _k8s_output() -> AgentOutput:
    return AgentOutput(
        agent_name=kubernetes_agent.AGENT_NAME,
        succeeded=True,
        evidence=[
            Evidence(
                source_agent=kubernetes_agent.AGENT_NAME,
                kind="pod_state",
                summary="payment-service-0 in CrashLoopBackOff, last termination OOMKilled (137), 12 restarts.",
                detail={"restart_count": 12, "last_exit_code": 137},
                severity=Severity.CRITICAL,
                collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
            )
        ],
    )


def _metrics_output() -> AgentOutput:
    return AgentOutput(
        agent_name=metrics_agent.AGENT_NAME,
        succeeded=True,
        evidence=[
            Evidence(
                source_agent=metrics_agent.AGENT_NAME,
                kind="resource_saturation",
                summary="Memory grew 256MiB → 1024MiB in 15min on payment-service-0.",
                detail={"baseline_bytes": 268435456, "peak_bytes": 1073741824},
                severity=Severity.CRITICAL,
                collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
            )
        ],
    )


def _logs_output() -> AgentOutput:
    return AgentOutput(
        agent_name=logs_agent.AGENT_NAME,
        succeeded=True,
        evidence=[
            Evidence(
                source_agent=logs_agent.AGENT_NAME,
                kind="exception_pattern",
                summary="23 java.lang.OutOfMemoryError stack traces (runtime=java).",
                detail={
                    "runtime": "java",
                    "count": 23,
                    "exception_class": "java.lang.OutOfMemoryError",
                },
                severity=Severity.CRITICAL,
                collected_at=datetime(2026, 6, 23, 10, 8, tzinfo=UTC),
            )
        ],
    )


def _expected_rca() -> RCAReport:
    return RCAReport(
        root_cause="JVM heap exhaustion in payment-service: OOMKilled corroborated by memory saturation and OutOfMemoryError stack traces.",
        root_cause_category="OOMKilled",
        confidence=0.92,
        evidence_refs=[0, 1, 2],
        reasoning="Three specialists corroborate the same mechanism.",
        recommendations=[
            "Roll back payment-service to the previous version",
            "Increase memory limit to 2Gi as short-term mitigation",
            "Investigate cache or allocation growth in the new code path",
        ],
    )


def _expected_recommendations_payload() -> str:
    from kubepilot_orch.agents.recommendation_agent import _RecommendationList
    from kubepilot_orch.state import Recommendation

    recs = _RecommendationList(
        recommendations=[
            Recommendation(
                title="Roll back deployment",
                rationale="Restore the previous image before the OOM regression.",
                commands=["kubectl rollout undo deployment/payment-service -n prod"],
                risk="medium",
                reversibility="reversible",
                priority=1,
                requires_approval=True,
            ),
            Recommendation(
                title="Raise memory limit",
                rationale="Short-term mitigation while investigating the leak.",
                commands=[
                    "kubectl set resources deployment/payment-service -n prod --limits=memory=2Gi"
                ],
                risk="low",
                reversibility="reversible",
                priority=2,
                requires_approval=True,
            ),
        ]
    )
    return recs.model_dump_json()


def _build_dispatcher() -> tuple[
    ScriptedLLM, ScriptedLLM, ScriptedLLM, ScriptedLLM, ScriptedLLM, object
]:
    k8s = ScriptedLLM(
        name="k8s",
        responses=[
            llm_tool_call("list_pods", {"namespace": "prod"}, call_id="k1"),
            llm_text("done"),
            llm_text(_k8s_output().model_dump_json()),
        ],
    )
    metrics = ScriptedLLM(
        name="metrics",
        responses=[
            llm_tool_call(
                "query_range", {"promql": "container_memory_working_set_bytes"}, call_id="m1"
            ),
            llm_text("done"),
            llm_text(_metrics_output().model_dump_json()),
        ],
    )
    logs = ScriptedLLM(
        name="logs",
        responses=[
            llm_tool_call("search_exceptions", {"namespace": "prod"}, call_id="l1"),
            llm_text("done"),
            llm_text(_logs_output().model_dump_json()),
        ],
    )
    rca = ScriptedLLM(
        name="rca",
        responses=[llm_text(_expected_rca().model_dump_json())],
    )
    recommendation = ScriptedLLM(
        name="recommendation",
        responses=[llm_text(_expected_recommendations_payload())],
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

        async def chat(
            self,
            messages,
            *,
            model,
            tools=None,
            response_schema=None,
            temperature=0.0,
            max_tokens=None,
        ):  # type: ignore[no-untyped-def]
            sys = next((m.content for m in messages if m.role == "system"), "")
            for keyword, llm in by_keyword:
                if keyword in sys:
                    return await llm.chat(
                        messages,
                        model=model,
                        tools=tools,
                        response_schema=response_schema,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
            raise AssertionError(f"No scripted LLM matched system prompt: {sys[:120]!r}")

    return k8s, metrics, logs, rca, recommendation, Dispatcher()


# ----------------------------------------------------------------------------
# The W6 acceptance test
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_investigation_produces_rca_report() -> None:
    k8s_llm, metrics_llm, logs_llm, rca_llm, rec_llm, dispatcher = _build_dispatcher()

    deps = AgentDeps(
        llm=build_router(dispatcher),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_k8s_handler, server_name="mcp-k8s"),
        mcp_prom=build_mcp_client(_prom_handler, server_name="mcp-prom"),
        mcp_loki=build_mcp_client(_loki_handler, server_name="mcp-loki"),
    )

    try:
        graph = build_graph(deps)
        initial = {
            "incident_id": uuid.uuid4(),
            "query": "why is payment-service failing?",
            "namespace": "prod",
            "service": "payment-service",
            "started_at": datetime.now(UTC),
        }
        final = await graph.ainvoke(initial)
    finally:
        await deps.mcp_k8s.aclose()
        await deps.mcp_prom.aclose()
        await deps.mcp_loki.aclose()

    # ---- All three specialists ran + RCA + recommendation recorded --------
    assert set(final["agent_outputs"].keys()) == {"kubernetes", "metrics", "logs"}
    assert set(final["completed_agents"]) == {
        "kubernetes",
        "metrics",
        "logs",
        rca_agent.AGENT_NAME,
        "recommendation",
    }

    # ---- Recommendations were produced with concrete commands -------------
    recs = final["recommendations"]
    assert recs, "Recommendation agent produced no recommendations"
    assert all(r.requires_approval for r in recs)
    assert any("kubectl" in cmd for r in recs for cmd in r.commands)

    # ---- Merged evidence has items from all three specialists -------------
    by_source = {a: [] for a in ("kubernetes", "metrics", "logs")}
    for ev in final["evidence"]:
        if ev.source_agent in by_source:
            by_source[ev.source_agent].append(ev)
    for agent, items in by_source.items():
        assert items, f"No evidence from {agent} in merged state"

    # ---- RCA report present and correctly shaped --------------------------
    rca = final["rca"]
    assert rca is not None
    assert rca.root_cause_category == "OOMKilled"
    assert rca.confidence >= 0.85
    assert "OOMKilled" in rca.root_cause or "heap" in rca.root_cause.lower()
    assert len(rca.recommendations) >= 1
    assert len(rca.recommendations) <= 4
    # evidence_refs only cite valid indices
    assert all(0 <= i < len(final["evidence"]) for i in rca.evidence_refs)

    # ---- Terminal state stamps --------------------------------------------
    assert final["current_step"] == "completed"
    assert final["finished_at"] is not None
    assert final["confidence"] == rca.confidence

    # ---- All scripted LLMs fully consumed (no test-setup drift) -----------
    for name, llm in [
        ("k8s", k8s_llm),
        ("metrics", metrics_llm),
        ("logs", logs_llm),
        ("rca", rca_llm),
        ("recommendation", rec_llm),
    ]:
        assert not llm.responses, f"{name} agent did not consume all scripted responses"


@pytest.mark.asyncio
async def test_end_to_end_investigation_runs_specialists_in_parallel() -> None:
    """Reducer semantics: three specialists all contribute evidence without overwrites."""
    *_, dispatcher = _build_dispatcher()

    deps = AgentDeps(
        llm=build_router(dispatcher),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_k8s_handler, server_name="mcp-k8s"),
        mcp_prom=build_mcp_client(_prom_handler, server_name="mcp-prom"),
        mcp_loki=build_mcp_client(_loki_handler, server_name="mcp-loki"),
    )

    try:
        graph = build_graph(deps)
        final = await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": "why is payment-service failing?",
                "namespace": "prod",
                "service": "payment-service",
                "started_at": datetime.now(UTC),
            }
        )
    finally:
        await deps.mcp_k8s.aclose()
        await deps.mcp_prom.aclose()
        await deps.mcp_loki.aclose()

    # Specifically: the evidence list has at least one item from each parallel branch.
    # If reducer semantics broke, some agents' evidence would be missing.
    sources = {e.source_agent for e in final["evidence"]}
    assert {"kubernetes", "metrics", "logs"}.issubset(sources)
