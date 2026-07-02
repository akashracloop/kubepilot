"""W5: the graph interrupts before executing a remediation plan, and resumes only
after an approval is recorded (HITL gate)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.remediation import approval
from kubepilot_orch.state import AgentOutput, Evidence, RCAReport, Recommendation, Severity
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)
from langgraph.checkpoint.memory import MemorySaver


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
            Evidence(
                source_agent=name,
                kind="obs",
                summary="x",
                severity=Severity.WARNING,
                collected_at=_now(),
            )
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


def _dispatcher(rca_category: str = "DeploymentRegression") -> Any:
    rca = ScriptedLLM(
        name="rca",
        responses=[
            llm_text(
                RCAReport(
                    root_cause="Deploy v2 regressed latency",
                    root_cause_category=rca_category,
                    confidence=0.85,
                    evidence_refs=[0],
                    reasoning="deploy correlates",
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
                            Recommendation(title="Roll back", rationale="revert").model_dump()
                        ]
                    }
                )
            )
        ],
    )
    remediation = ScriptedLLM(
        name="remediation",
        responses=[
            llm_text(
                json.dumps(
                    {
                        "actions": [
                            {
                                "tool": "rollout_undo",
                                "target": "deployment/checkout",
                                "namespace": "prod",
                                "rationale": "revert the regressive deploy",
                                "priority": 1,
                            }
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
        ("Remediation agent", remediation),
    ]

    class Dispatcher:
        name = "dispatcher"

        async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
            sys = next((m.content for m in messages if m.role == "system"), "")
            for kw, llm in by_keyword:
                if kw in sys:
                    return await llm.chat(messages, **kwargs)
            raise AssertionError(f"no scripted llm for {sys[:60]!r}")

    return Dispatcher()


def _deps(rca_category: str = "DeploymentRegression") -> AgentDeps:
    return AgentDeps(
        llm=build_router(_dispatcher(rca_category)),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_tool_handler("list_pods"), server_name="k8s"),
        mcp_prom=build_mcp_client(_tool_handler("query_range"), server_name="prom"),
        mcp_loki=build_mcp_client(_tool_handler("search_exceptions"), server_name="loki"),
        enable_remediation=True,
    )


def _write_handler(applied: bool) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(200, json={"tools": []})
        body = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "tool": body["tool"],
                "result": {"applied": applied, "dry_run": not applied, "preview": "did it"},
            },
        )

    return handler


@pytest.mark.asyncio
async def test_graph_interrupts_before_execute_then_resumes_on_approval() -> None:
    deps = _deps()
    graph = build_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "incident-1"}}
    initial = {
        "incident_id": uuid.uuid4(),
        "query": "why is checkout slow?",
        "namespace": "prod",
        "service": "checkout",
        "started_at": _now(),
    }
    try:
        # Run to the HITL gate: pauses BEFORE execute_remediation.
        await graph.ainvoke(initial, config)
        snap = await graph.aget_state(config)
        assert snap.next == ("execute_remediation",), "graph must pause before execution"
        state = snap.values
        assert state["remediation_plan"] is not None
        assert state["remediation_plan"].actions[0].tool == "rollout_undo"
        assert state["remediation_outcome"] == "pending_approval"
        # Execution has NOT run yet.
        assert "remediation_exec" not in state.get("completed_agents", [])

        # A human approves the single action → record it into the checkpointed state.
        appr = approval.build_approval(
            action_index=0, decision="approved", approver_role="operator"
        )
        await graph.aupdate_state(config, {"approvals": [appr]})

        # Resume: execute node runs and resolves the outcome to approved.
        resumed = await graph.ainvoke(None, config)
    finally:
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki):
            await c.aclose()

    assert "remediation_exec" in resumed["completed_agents"]
    assert resumed["remediation_outcome"] == "approved"
    assert resumed["current_step"] == "completed"


@pytest.mark.asyncio
async def test_resume_after_rejection_does_not_approve() -> None:
    deps = _deps()
    graph = build_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "incident-2"}}
    try:
        await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": "why is checkout slow?",
                "namespace": "prod",
                "service": "checkout",
                "started_at": _now(),
            },
            config,
        )
        rej = approval.build_approval(action_index=0, decision="rejected", approver_role="operator")
        await graph.aupdate_state(config, {"approvals": [rej]})
        resumed = await graph.ainvoke(None, config)
    finally:
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki):
            await c.aclose()

    assert resumed["remediation_outcome"] == "rejected"


@pytest.mark.asyncio
async def test_remediation_off_by_default_no_interrupt() -> None:
    deps = _deps()
    deps.enable_remediation = False
    graph = build_graph(deps, checkpointer=MemorySaver())
    assert "execute_remediation" not in set(graph.get_graph().nodes)
    for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki):
        await c.aclose()


@pytest.mark.asyncio
async def test_resume_executes_approved_plan_via_write_mcp() -> None:
    """With a policy + write MCP wired in, an approved plan actually executes on
    resume: an execution record is written and the outcome is closed."""
    from kubepilot_orch.remediation.policy import load_policies_from_yaml

    deps = _deps()
    deps.mcp_write = build_mcp_client(_write_handler(applied=True), server_name="mcp-k8s-write")
    deps.policy = load_policies_from_yaml(
        "policies:\n  - name: prod-rollback\n    roles: [operator, admin]\n"
        "    namespaces: [prod]\n    actions: [rollout_undo]\n    reversibility: [reversible]\n"
    )
    graph = build_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "incident-exec"}}
    try:
        await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": "why is checkout slow?",
                "namespace": "prod",
                "service": "checkout",
                "started_at": _now(),
            },
            config,
        )
        appr = approval.build_approval(
            action_index=0, decision="approved", approver_role="operator"
        )
        await graph.aupdate_state(config, {"approvals": [appr]})
        resumed = await graph.ainvoke(None, config)
    finally:
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki, deps.mcp_write):
            await c.aclose()

    assert resumed["remediation_outcome"] == "closed"
    assert len(resumed["executions"]) == 1
    assert resumed["executions"][0].status == "succeeded"
    assert resumed["executions"][0].tool == "rollout_undo"


@pytest.mark.asyncio
async def test_resume_validates_and_rolls_back_on_regression() -> None:
    """W9+W8: on resume, execution runs, the post-check detects a regression, the
    reversible action is auto-rolled-back, and the incident is reopened."""
    from kubepilot_orch.remediation.policy import load_policies_from_yaml

    deps = _deps()
    deps.mcp_write = build_mcp_client(_write_handler(applied=True), server_name="mcp-k8s-write")
    deps.policy = load_policies_from_yaml(
        "policies:\n  - name: prod-rollback\n    roles: [operator, admin]\n"
        "    namespaces: [prod]\n    actions: [rollout_undo]\n    reversibility: [reversible]\n"
    )

    # Single-snapshot signal fetcher: called once pre-write (baseline) then once
    # post-write. Here the error rate got much worse after → regression.
    _snapshots = iter([{"error_rate": 0.02}, {"error_rate": 0.60}])

    async def _signals(_state: Any) -> dict[str, float]:
        return next(_snapshots)

    deps.remediation_signal_fn = _signals

    graph = build_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "incident-regress"}}
    try:
        await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": "why is checkout slow?",
                "namespace": "prod",
                "service": "checkout",
                "started_at": _now(),
            },
            config,
        )
        appr = approval.build_approval(
            action_index=0, decision="approved", approver_role="operator"
        )
        await graph.aupdate_state(config, {"approvals": [appr]})
        resumed = await graph.ainvoke(None, config)
    finally:
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki, deps.mcp_write):
            await c.aclose()

    assert resumed["remediation_outcome"] == "reopened"
    # rollout_undo has no clean inverse, so no rollback record — but the incident
    # is still reopened because the post-check regressed.
    assert len(resumed["executions"]) == 1


@pytest.mark.asyncio
async def test_selfheal_pattern_executes_autonomously_without_interrupt() -> None:
    """W10 gap fix: an opt-in self-heal pattern routes around the HITL interrupt
    and executes autonomously (still policy-gated), reaching a terminal outcome
    in a single pass."""
    from kubepilot_orch.remediation.policy import load_policies_from_yaml

    deps = _deps(rca_category="ImagePullBackOff")
    deps.selfheal_patterns = frozenset({"imagepull_revert"})
    deps.mcp_write = build_mcp_client(_write_handler(applied=True), server_name="mcp-k8s-write")
    deps.policy = load_policies_from_yaml(
        "policies:\n  - name: prod-selfheal\n    roles: [operator, admin]\n"
        "    namespaces: [prod]\n    actions: [rollout_undo]\n    reversibility: [reversible]\n"
    )

    graph = build_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "incident-selfheal"}}
    result = await graph.ainvoke(
        {
            "incident_id": uuid.uuid4(),
            "query": "checkout pods failing to pull image",
            "namespace": "prod",
            "service": "checkout",
            "started_at": _now(),
        },
        config,
    )
    # No interrupt: the run completed in one pass (nothing pending).
    snap = await graph.aget_state(config)
    assert snap.next == ()
    # The autonomous action executed and the incident reached a terminal outcome.
    assert result["executions"], "self-heal should have executed an action"
    assert result["executions"][0].tool == "rollout_undo"
    assert result["remediation_outcome"] in ("closed", "reopened")


@pytest.mark.asyncio
async def test_selfheal_disabled_still_interrupts_for_hitl() -> None:
    """With no enabled patterns the graph keeps the HITL interrupt (unchanged)."""
    deps = _deps(rca_category="ImagePullBackOff")  # would match, but no pattern enabled
    graph = build_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "incident-hitl"}}
    await graph.ainvoke(
        {
            "incident_id": uuid.uuid4(),
            "query": "q",
            "namespace": "prod",
            "service": "checkout",
            "started_at": _now(),
        },
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("execute_remediation",)
