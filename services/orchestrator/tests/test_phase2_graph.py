"""Phase 2: the graph fans out to 5 specialists when tempo + ci MCP are wired in.

Mirrors test_end_to_end_investigation.py but adds the Tracing + Deployment
branches and asserts they run and contribute evidence, and that the graph stays
Phase-1-shaped (3 specialists) when tempo/ci deps are absent.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.state import AgentOutput, Evidence, RCAReport, Recommendation
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)


def _one_tool_handler(tool_name: str, result: object) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": tool_name,
                            "description": tool_name,
                            "parameters": {
                                "type": "object",
                                "properties": {"service": {"type": "string"}},
                            },
                        }
                    ]
                },
            )
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": result})

    return handler


def _now() -> datetime:
    return datetime(2026, 7, 2, 10, 8, tzinfo=UTC)


def _evidence(agent: str, kind: str, summary: str) -> Evidence:
    return Evidence(source_agent=agent, kind=kind, summary=summary, collected_at=_now())


def _specialist_scripts() -> dict[str, ScriptedLLM]:
    """One ScriptedLLM per specialist: [tool_call, end-of-loop text, structured summary]."""

    def spec(name: str, tool: str, kind: str, summary: str) -> ScriptedLLM:
        out = AgentOutput(
            agent_name=name, succeeded=True, evidence=[_evidence(name, kind, summary)]
        )
        return ScriptedLLM(
            name=name,
            responses=[
                llm_tool_call(tool, {"service": "checkout-service"}, call_id=f"{name}-1"),
                llm_text("done"),
                llm_text(out.model_dump_json()),
            ],
        )

    return {
        "kubernetes": spec("kubernetes", "list_pods", "pod_state", "pods healthy"),
        "metrics": spec("metrics", "query_range", "metric_anomaly", "latency up"),
        "logs": spec("logs", "search_exceptions", "log_pattern", "no exceptions"),
        "tracing": spec("tracing", "query_traces", "latency_hotspot", "payments-db slow"),
        "deployment": spec(
            "deployment", "get_deployment_history", "recent_deploy", "v2.3.1 8m before"
        ),
    }


def _dispatcher(scripts: dict[str, ScriptedLLM]) -> object:
    rca = ScriptedLLM(
        name="rca",
        responses=[
            llm_text(
                RCAReport(
                    root_cause="Deploy v2.3.1 slow query",
                    root_cause_category="LatencyRegression",
                    confidence=0.88,
                    evidence_refs=[0],
                    reasoning="trace + deploy correlate",
                    recommendations=["Roll back"],
                ).model_dump_json()
            )
        ],
    )
    rec = ScriptedLLM(
        name="rec",
        responses=[
            llm_text(
                json.dumps(
                    {
                        "recommendations": [
                            Recommendation(
                                title="Roll back deployment",
                                rationale="restore previous image",
                                commands=[
                                    "kubectl rollout undo deployment/checkout-service -n prod"
                                ],
                                priority=1,
                            ).model_dump()
                        ]
                    }
                )
            )
        ],
    )
    by_keyword = [
        ("Kubernetes specialist", scripts["kubernetes"]),
        ("metrics specialist", scripts["metrics"]),
        ("logs specialist", scripts["logs"]),
        ("Tracing specialist", scripts["tracing"]),
        ("Deployment specialist", scripts["deployment"]),
        ("Root-Cause Analysis", rca),
        ("Recommendation agent", rec),
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
            raise AssertionError(f"No scripted LLM for system prompt: {sys[:80]!r}")

    return Dispatcher()


@pytest.mark.asyncio
async def test_five_specialists_run_when_tempo_and_ci_present() -> None:
    scripts = _specialist_scripts()
    deps = AgentDeps(
        llm=build_router(_dispatcher(scripts)),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(
            _one_tool_handler("list_pods", [{"name": "p"}]), server_name="k8s"
        ),
        mcp_prom=build_mcp_client(
            _one_tool_handler("query_range", {"series": []}), server_name="prom"
        ),
        mcp_loki=build_mcp_client(
            _one_tool_handler("search_exceptions", {"total": 0}), server_name="loki"
        ),
        mcp_tempo=build_mcp_client(
            _one_tool_handler("query_traces", {"trace_id": "t1"}), server_name="tempo"
        ),
        mcp_ci=build_mcp_client(
            _one_tool_handler("get_deployment_history", {"deployments": []}), server_name="ci"
        ),
    )
    try:
        graph = build_graph(deps)
        final = await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": "why is checkout-service slow?",
                "namespace": "prod",
                "service": "checkout-service",
                "started_at": _now(),
            }
        )
    finally:
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki, deps.mcp_tempo, deps.mcp_ci):
            assert c is not None
            await c.aclose()

    assert {"kubernetes", "metrics", "logs", "tracing", "deployment"}.issubset(
        set(final["completed_agents"])
    )
    sources = {e.source_agent for e in final["evidence"]}
    assert {"tracing", "deployment"}.issubset(sources)
    assert final["rca"].root_cause_category == "LatencyRegression"
    assert final["current_step"] == "completed"


def test_graph_is_phase1_shaped_without_tempo_ci() -> None:
    # No mcp_tempo / mcp_ci → only the 3 core specialists; graph still compiles.
    deps = AgentDeps(
        llm=build_router(ScriptedLLM(name="x", responses=[])),
        mcp_k8s=build_mcp_client(_one_tool_handler("list_pods", []), server_name="k8s"),
        mcp_prom=build_mcp_client(_one_tool_handler("query_range", {}), server_name="prom"),
        mcp_loki=build_mcp_client(_one_tool_handler("search_exceptions", {}), server_name="loki"),
    )
    graph = build_graph(deps)
    node_names = set(graph.get_graph().nodes)
    assert "tracing" not in node_names
    assert "deployment" not in node_names
    assert {"kubernetes", "metrics", "logs", "rca"}.issubset(node_names)
