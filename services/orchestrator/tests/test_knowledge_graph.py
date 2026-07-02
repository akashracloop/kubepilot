"""W5: the knowledge node runs before RCA and its context reaches the RCA prompt."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from kubepilot_orch.agents import knowledge_agent
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.knowledge import (
    InMemoryKnowledgeStore,
    KnowledgeRetriever,
    ingest_snapshot,
)
from kubepilot_orch.knowledge.ingest import OWNER_ANNOTATION
from kubepilot_orch.state import AgentOutput, Evidence, RCAReport, Recommendation
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)

_SNAPSHOT = {
    "services": [
        {
            "service": "checkout-service",
            "namespace": "prod",
            "annotations": {OWNER_ANNOTATION: "payments-team"},
            "dependencies": ["payments-db"],
            "slos": {"p99_latency_ms": 500},
        },
        {"service": "payments-db", "namespace": "prod", "owner": "data-team"},
    ]
}


def _now() -> datetime:
    return datetime(2026, 7, 2, 10, 8, tzinfo=UTC)


def _tool_handler(tool: str) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(
                200,
                json={
                    "tools": [{"name": tool, "description": tool, "parameters": {"type": "object"}}]
                },
            )
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": {"ok": True}})

    return handler


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


@pytest.mark.asyncio
async def test_knowledge_runs_before_rca_and_reaches_the_prompt() -> None:
    store = InMemoryKnowledgeStore()
    await ingest_snapshot(store, _SNAPSHOT)
    retriever = KnowledgeRetriever(store)

    rca_prompts: list[str] = []

    def _rca_response(*, messages: list[Any], **_: Any) -> Any:
        rca_prompts.append("\n".join(m.content for m in messages if m.role == "user"))
        return llm_text(
            RCAReport(
                root_cause="payments-db latency is dragging checkout-service; page payments-team.",
                root_cause_category="DependencyFailure",
                confidence=0.88,
                evidence_refs=[0],
                reasoning="A known dependency (payments-db) is the suspect.",
                recommendations=["Investigate payments-db"],
            ).model_dump_json()
        )

    rca = ScriptedLLM(name="rca", responses=[_rca_response])
    rec = ScriptedLLM(
        name="rec",
        responses=[
            llm_text(
                json.dumps(
                    {
                        "recommendations": [
                            Recommendation(title="Check payments-db", rationale="dep").model_dump()
                        ]
                    }
                )
            )
        ],
    )
    by_keyword = [
        ("Kubernetes specialist", _spec("kubernetes", "list_pods")),
        ("metrics specialist", _spec("metrics", "query_range")),
        ("logs specialist", _spec("logs", "search_exceptions")),
        ("Root-Cause Analysis", rca),
        ("Recommendation agent", rec),
    ]

    class Dispatcher:
        name = "dispatcher"

        async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
            sys = next((m.content for m in messages if m.role == "system"), "")
            for kw, llm in by_keyword:
                if kw in sys:
                    return await llm.chat(messages, **kwargs)
            raise AssertionError(f"no scripted llm for {sys[:60]!r}")

    deps = AgentDeps(
        llm=build_router(Dispatcher()),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_tool_handler("list_pods"), server_name="k8s"),
        mcp_prom=build_mcp_client(_tool_handler("query_range"), server_name="prom"),
        mcp_loki=build_mcp_client(_tool_handler("search_exceptions"), server_name="loki"),
        knowledge=retriever,
    )
    try:
        graph = build_graph(deps)
        assert "knowledge" in set(graph.get_graph().nodes)
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

    # Knowledge node ran and populated context before RCA.
    assert "knowledge" in final["completed_agents"]
    ctx = final["knowledge_context"]
    assert next(f.service for f in ctx) == "checkout-service"
    assert {f.service for f in ctx} == {"checkout-service", "payments-db"}

    # W5 acceptance: the owning team + the known dependency reached the RCA prompt.
    assert rca_prompts, "RCA was never called"
    prompt = rca_prompts[0]
    assert "Cluster knowledge" in prompt
    assert "payments-team" in prompt  # owning team
    assert "payments-db" in prompt  # known dependency


@pytest.mark.asyncio
async def test_graph_omits_knowledge_node_when_absent() -> None:
    deps = AgentDeps(
        llm=build_router(ScriptedLLM(name="noop")),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_tool_handler("list_pods"), server_name="k8s"),
        mcp_prom=build_mcp_client(_tool_handler("query_range"), server_name="prom"),
        mcp_loki=build_mcp_client(_tool_handler("search_exceptions"), server_name="loki"),
    )
    try:
        graph = build_graph(deps)
        assert "knowledge" not in set(graph.get_graph().nodes)
    finally:
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki):
            await c.aclose()


def test_to_state_update_shape() -> None:
    update = knowledge_agent.to_state_update([])
    assert update["knowledge_context"] == []
    assert update["current_step"] == "knowledge_retrieved"
    assert update["completed_agents"] == ["knowledge"]


@pytest.mark.asyncio
async def test_memory_and_knowledge_both_enabled_run_serially() -> None:
    """Regression: memory + knowledge both on must not concurrently write the
    singleton current_step (LangGraph InvalidUpdateError). They run serially."""
    from kubepilot_orch.memory import HashEmbedder, InMemoryMemoryStore, MemoryRetriever

    store = InMemoryKnowledgeStore()
    await ingest_snapshot(store, _SNAPSHOT)
    knowledge = KnowledgeRetriever(store)

    mem = MemoryRetriever(HashEmbedder(dim=256), InMemoryMemoryStore())
    await mem.index(
        incident_id=uuid.uuid4(),
        summary="checkout-service slow after deploy",
        root_cause_category="DependencyFailure",
        namespace="prod",
        service="checkout-service",
    )

    rca = ScriptedLLM(
        name="rca",
        responses=[
            llm_text(
                RCAReport(
                    root_cause="payments-db dependency",
                    root_cause_category="DependencyFailure",
                    confidence=0.8,
                    evidence_refs=[0],
                    reasoning="dep",
                    recommendations=["check payments-db"],
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
                            Recommendation(title="Check dep", rationale="x").model_dump()
                        ]
                    }
                )
            )
        ],
    )
    by_keyword = [
        ("Kubernetes specialist", _spec("kubernetes", "list_pods")),
        ("metrics specialist", _spec("metrics", "query_range")),
        ("logs specialist", _spec("logs", "search_exceptions")),
        ("Root-Cause Analysis", rca),
        ("Recommendation agent", rec),
    ]

    class Dispatcher:
        name = "dispatcher"

        async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
            sys = next((m.content for m in messages if m.role == "system"), "")
            for kw, llm in by_keyword:
                if kw in sys:
                    return await llm.chat(messages, **kwargs)
            raise AssertionError(f"no scripted llm for {sys[:60]!r}")

    deps = AgentDeps(
        llm=build_router(Dispatcher()),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_tool_handler("list_pods"), server_name="k8s"),
        mcp_prom=build_mcp_client(_tool_handler("query_range"), server_name="prom"),
        mcp_loki=build_mcp_client(_tool_handler("search_exceptions"), server_name="loki"),
        memory=mem,
        knowledge=knowledge,
    )
    try:
        graph = build_graph(deps)
        nodes = set(graph.get_graph().nodes)
        assert {"memory", "knowledge"}.issubset(nodes)
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

    # Completed without a concurrent-update error; both collectors contributed.
    assert final["current_step"] == "completed"
    assert "memory" in final["completed_agents"]
    assert "knowledge" in final["completed_agents"]
    assert len(final["knowledge_context"]) >= 1
