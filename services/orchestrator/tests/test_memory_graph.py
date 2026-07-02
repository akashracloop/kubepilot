"""W6: memory retrieval runs before RCA and the concluded incident is indexed."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from kubepilot_orch.agents import memory_agent
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.memory import HashEmbedder, InMemoryMemoryStore, MemoryRetriever
from kubepilot_orch.state import (
    AgentOutput,
    Evidence,
    InvestigationState,
    RCAReport,
    Recommendation,
)
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)


def _tool_handler(tool: str) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": tool,
                            "description": tool,
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ]
                },
            )
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": {"ok": True}})

    return handler


def _now() -> datetime:
    return datetime(2026, 7, 2, 10, 8, tzinfo=UTC)


def _spec(name: str, tool: str) -> ScriptedLLM:
    out = AgentOutput(
        agent_name=name,
        succeeded=True,
        evidence=[
            Evidence(source_agent=name, kind="obs", summary=f"{name} ok", collected_at=_now())
        ],
    )
    return ScriptedLLM(
        name=name,
        responses=[
            llm_tool_call(tool, {}, call_id=f"{name}-1"),
            llm_text("done"),
            llm_text(out.model_dump_json()),
        ],
    )


def _dispatcher() -> object:
    scripts = {
        "kubernetes": _spec("kubernetes", "list_pods"),
        "metrics": _spec("metrics", "query_range"),
        "logs": _spec("logs", "search_exceptions"),
    }
    rca = ScriptedLLM(
        name="rca",
        responses=[
            llm_text(
                RCAReport(
                    root_cause="Latency regression from a recent deploy",
                    root_cause_category="LatencyRegression",
                    confidence=0.9,
                    evidence_refs=[0],
                    reasoning="signals + a similar past incident corroborate",
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
                            Recommendation(title="Roll back", rationale="restore").model_dump()
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
            for kw, llm in by_keyword:
                if kw in sys:
                    return await llm.chat(
                        messages,
                        model=model,
                        tools=tools,
                        response_schema=response_schema,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
            raise AssertionError(f"no scripted llm for {sys[:60]!r}")

    return Dispatcher()


@pytest.mark.asyncio
async def test_memory_retrieved_before_rca_and_indexed_on_finalize() -> None:
    store = InMemoryMemoryStore()
    retriever = MemoryRetriever(HashEmbedder(dim=256), store)
    # Seed one similar past incident on the same service.
    await retriever.index(
        incident_id=uuid.uuid4(),
        summary="why is checkout-service slow? | service=checkout-service | root_cause: latency regression from a deploy",
        root_cause_category="LatencyRegression",
        namespace="prod",
        service="checkout-service",
        outcome="rolled back the deploy",
    )
    assert len(store) == 1

    deps = AgentDeps(
        llm=build_router(_dispatcher()),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_tool_handler("list_pods"), server_name="k8s"),
        mcp_prom=build_mcp_client(_tool_handler("query_range"), server_name="prom"),
        mcp_loki=build_mcp_client(_tool_handler("search_exceptions"), server_name="loki"),
        memory=retriever,
    )
    try:
        graph = build_graph(deps)
        assert "memory" in set(graph.get_graph().nodes)
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
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki):
            await c.aclose()

    # Memory ran and populated context before RCA.
    assert "memory" in final["completed_agents"]
    assert len(final["memory_context"]) >= 1
    assert final["memory_context"][0].service == "checkout-service"
    assert final["memory_context"][0].outcome == "rolled back the deploy"
    # The concluded incident was indexed at finalize (store grew from 1 to 2).
    assert len(store) == 2


def test_memory_query_and_summary_builders() -> None:
    state = InvestigationState(
        incident_id=uuid.uuid4(),
        query="why is checkout-service slow?",
        namespace="prod",
        service="checkout-service",
        started_at=_now(),
        evidence=[
            Evidence(
                source_agent="tracing",
                kind="latency_hotspot",
                summary="payments-db slow",
                collected_at=_now(),
            )
        ],
        rca=RCAReport(
            root_cause="N+1 query",
            root_cause_category="LatencyRegression",
            confidence=0.9,
            reasoning="x",
        ),
    )
    q = memory_agent.build_query(state)
    assert "checkout-service" in q and "payments-db slow" in q
    s = memory_agent.incident_summary(state)
    assert "N+1 query" in s and "LatencyRegression" in s
