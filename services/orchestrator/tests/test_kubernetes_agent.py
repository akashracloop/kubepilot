"""W4 acceptance test for the Kubernetes agent.

Spec from PHASE_1_PLAN.md W4:
    "Unit test: agent on a CrashLoopBackOff fixture returns correct pod state summary"

We script a fake LLM (issues realistic tool_calls in sequence, then produces the
structured AgentOutput) and a fake mcp-k8s (returns canned tool results). The
agent should orchestrate them into a final AgentOutput with evidence about the
CrashLoopBackOff / OOMKilled symptoms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from kubepilot_orch.agents import kubernetes_agent
from kubepilot_orch.agents.kubernetes_agent import AGENT_NAME
from kubepilot_orch.config import LLMRoleBinding
from kubepilot_orch.llm.base import (
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolSchema,
)
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.state import AgentOutput, Evidence, Severity
from pydantic import BaseModel

# ----------------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------------


@dataclass
class ScriptedLLM:
    """Fake LLMProvider that returns pre-scripted LLMResponses in order.

    Each entry is either an LLMResponse OR a callable(messages, tools, ...) -> LLMResponse
    for asserting on the inputs at each step.
    """

    name: str = "scripted"
    responses: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[ToolSchema] | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "response_schema": response_schema,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of scripted responses")
        item = self.responses.pop(0)
        if callable(item):
            return item(messages=messages, tools=tools, response_schema=response_schema)
        return item


def _make_mcp_client(handler) -> MCPClient:  # type: ignore[no-untyped-def]
    client = MCPClient(server_name="mcp-k8s", base_url="http://mcp-k8s")
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://mcp-k8s"
    )
    return client


def _router(provider: ScriptedLLM) -> LLMRouter:
    return LLMRouter(
        providers={"scripted": provider},  # type: ignore[dict-item]
        role_bindings={
            Role.ANALYSIS: LLMRoleBinding(provider="scripted", model="scripted-pro"),
        },
    )


# ----------------------------------------------------------------------------
# Fixtures — the CrashLoopBackOff scenario
# ----------------------------------------------------------------------------


_TOOL_DESCRIPTORS = {
    "tools": [
        {
            "name": "list_pods",
            "description": "List pods in a namespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "label_selector": {"type": ["string", "null"]},
                },
                "required": ["namespace"],
            },
        },
        {
            "name": "describe_pod",
            "description": "Describe a pod in detail including recent events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["namespace", "name"],
            },
        },
        {
            "name": "get_events",
            "description": "Recent events in a namespace.",
            "parameters": {
                "type": "object",
                "properties": {"namespace": {"type": "string"}},
                "required": ["namespace"],
            },
        },
    ]
}


_LIST_PODS_RESULT = [
    {
        "name": "payment-service-0",
        "namespace": "prod",
        "phase": "Running",
        "status_reason": "CrashLoopBackOff",
        "node_name": "node-a",
        "pod_ip": "10.0.0.5",
        "host_ip": "192.168.1.10",
        "start_time": "2026-06-23T10:00:00Z",
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
]


_DESCRIBE_POD_RESULT = {
    **_LIST_PODS_RESULT[0],
    "spec": {"nodeName": "node-a", "containers": []},
    "conditions": [{"type": "Ready", "status": "False"}],
    "recent_events": [
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "count": 9,
            "first_seen": "2026-06-23T10:01:00Z",
            "last_seen": "2026-06-23T10:08:00Z",
            "involved_object_kind": "Pod",
            "involved_object_name": "payment-service-0",
            "involved_object_namespace": "prod",
            "source_component": "kubelet",
        }
    ],
}


def _mcp_handler(request: httpx.Request) -> httpx.Response:
    """Mock mcp-k8s — routes /mcp/tools, /mcp/invoke based on the body."""
    if request.url.path == "/mcp/tools":
        return httpx.Response(200, json=_TOOL_DESCRIPTORS)
    if request.url.path == "/mcp/invoke":
        body = json.loads(request.content.decode())
        tool = body["tool"]
        if tool == "list_pods":
            return httpx.Response(200, json={"tool": tool, "result": _LIST_PODS_RESULT})
        if tool == "describe_pod":
            return httpx.Response(200, json={"tool": tool, "result": _DESCRIBE_POD_RESULT})
        if tool == "get_events":
            return httpx.Response(
                200, json={"tool": tool, "result": _DESCRIBE_POD_RESULT["recent_events"]}
            )
        return httpx.Response(404, json={"detail": f"Unknown tool: {tool}"})
    return httpx.Response(404)


# ----------------------------------------------------------------------------
# The W4 acceptance test
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kubernetes_agent_diagnoses_crashloopbackoff() -> None:
    """Agent on a CrashLoopBackOff fixture must produce evidence describing the symptom."""

    expected_output = AgentOutput(
        agent_name=AGENT_NAME,
        succeeded=True,
        evidence=[
            Evidence(
                source_agent=AGENT_NAME,
                kind="pod_state",
                summary=(
                    "payment-service-0 is in CrashLoopBackOff; last termination OOMKilled "
                    "with exit code 137; 12 restarts."
                ),
                detail={
                    "pod": "payment-service-0",
                    "status_reason": "CrashLoopBackOff",
                    "last_termination_reason": "OOMKilled",
                    "last_exit_code": 137,
                    "restart_count": 12,
                },
                severity=Severity.CRITICAL,
                collected_at="2026-06-23T10:08:30Z",
            ),
            Evidence(
                source_agent=AGENT_NAME,
                kind="event",
                summary="Kubelet has been backing off restart of failed container (9 occurrences).",
                detail={"reason": "BackOff", "count": 9, "type": "Warning"},
                severity=Severity.ERROR,
                collected_at="2026-06-23T10:08:30Z",
            ),
        ],
        notes="payment-service-0 in CrashLoopBackOff with prior OOMKilled. Kubernetes-level only; root cause TBD by RCA agent.",
    )

    scripted = ScriptedLLM(
        responses=[
            # Iteration 1: LLM asks for list_pods
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="list_pods",
                        arguments={
                            "namespace": "prod",
                            "label_selector": "app=payment-service",
                        },
                    )
                ],
                input_tokens=120,
                output_tokens=40,
                model="scripted-pro",
                provider="scripted",
            ),
            # Iteration 2: LLM asks for describe_pod on the failing pod
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_2",
                        name="describe_pod",
                        arguments={"namespace": "prod", "name": "payment-service-0"},
                    )
                ],
                input_tokens=400,
                output_tokens=50,
                model="scripted-pro",
                provider="scripted",
            ),
            # Iteration 3: LLM is done (no tool_calls), short transcript note.
            LLMResponse(
                content="I have enough information to summarize.",
                tool_calls=[],
                input_tokens=600,
                output_tokens=20,
                model="scripted-pro",
                provider="scripted",
            ),
            # Phase 2: structured summary
            LLMResponse(
                content=expected_output.model_dump_json(),
                tool_calls=[],
                input_tokens=650,
                output_tokens=200,
                model="scripted-pro",
                provider="scripted",
            ),
        ]
    )

    mcp = _make_mcp_client(_mcp_handler)
    try:
        output = await kubernetes_agent.run(
            query="why is payment-service failing?",
            namespace="prod",
            service="payment-service",
            llm=_router(scripted),
            mcp_k8s=mcp,
        )
    finally:
        await mcp.aclose()

    # ---- Assertions on the produced output --------------------------------
    assert output.agent_name == AGENT_NAME
    assert output.succeeded is True
    assert output.tokens_used > 0
    assert output.latency_ms >= 0
    assert len(output.evidence) >= 2

    # The CrashLoopBackOff signal must be present in at least one evidence item.
    summaries = " ".join(e.summary.lower() for e in output.evidence)
    assert "crashloopbackoff" in summaries
    assert "oomkilled" in summaries
    assert any(e.severity in {Severity.CRITICAL, Severity.ERROR} for e in output.evidence)

    # Notes should not speculate about root cause.
    if output.notes:
        assert "rca" not in output.notes.lower().split() or "tbd" in output.notes.lower()


@pytest.mark.asyncio
async def test_kubernetes_agent_respects_max_iterations() -> None:
    """If the LLM keeps asking for tools forever, the agent must stop at MAX_ITERATIONS."""
    forever_tool_call = LLMResponse(
        content="",
        tool_calls=[ToolCall(id="x", name="list_pods", arguments={"namespace": "prod"})],
        input_tokens=10,
        output_tokens=10,
        model="m",
        provider="scripted",
    )

    # 1 response per iteration up to the cap, then a structured summary response.
    summary_payload = AgentOutput(
        agent_name=AGENT_NAME, succeeded=True, evidence=[], notes="ran out of iterations"
    ).model_dump_json()

    scripted = ScriptedLLM(
        responses=[forever_tool_call] * kubernetes_agent.MAX_ITERATIONS
        + [
            LLMResponse(
                content=summary_payload,
                tool_calls=[],
                input_tokens=10,
                output_tokens=10,
                model="m",
                provider="scripted",
            )
        ]
    )

    mcp = _make_mcp_client(_mcp_handler)
    try:
        output = await kubernetes_agent.run(
            query="why is x failing?",
            namespace="prod",
            service=None,
            llm=_router(scripted),
            mcp_k8s=mcp,
        )
    finally:
        await mcp.aclose()

    # We made MAX_ITERATIONS tool-loop calls + 1 summary call = MAX_ITERATIONS + 1 total.
    assert len(scripted.calls) == kubernetes_agent.MAX_ITERATIONS + 1
    assert output.agent_name == AGENT_NAME


@pytest.mark.asyncio
async def test_kubernetes_agent_records_tool_errors_as_evidence() -> None:
    """When mcp-k8s returns an error, the agent records it without crashing."""
    summary_payload = AgentOutput(
        agent_name=AGENT_NAME,
        succeeded=False,
        evidence=[],
        notes="tools failed",
    ).model_dump_json()

    scripted = ScriptedLLM(
        responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="call_1", name="describe_pod", arguments={"namespace": "prod"})
                ],
                input_tokens=10,
                output_tokens=10,
                model="m",
                provider="scripted",
            ),
            LLMResponse(
                content="failed to fetch — done",
                tool_calls=[],
                input_tokens=20,
                output_tokens=10,
                model="m",
                provider="scripted",
            ),
            LLMResponse(
                content=summary_payload,
                tool_calls=[],
                input_tokens=30,
                output_tokens=10,
                model="m",
                provider="scripted",
            ),
        ]
    )

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(200, json=_TOOL_DESCRIPTORS)
        return httpx.Response(400, json={"detail": "missing required argument: name"})

    mcp = _make_mcp_client(failing_handler)
    try:
        output = await kubernetes_agent.run(
            query="why is x failing?",
            namespace="prod",
            service=None,
            llm=_router(scripted),
            mcp_k8s=mcp,
        )
    finally:
        await mcp.aclose()

    assert any(e.kind == "tool_error" for e in output.evidence)
    tool_err = next(e for e in output.evidence if e.kind == "tool_error")
    assert tool_err.severity == Severity.WARNING
    assert "describe_pod" in tool_err.summary


@pytest.mark.asyncio
async def test_kubernetes_agent_recovers_from_invalid_summary_json() -> None:
    """If the LLM produces unparseable JSON for the summary, agent records failure."""
    scripted = ScriptedLLM(
        responses=[
            LLMResponse(
                content="no tools needed",
                tool_calls=[],
                input_tokens=10,
                output_tokens=10,
                model="m",
                provider="scripted",
            ),
            LLMResponse(
                content="not json at all",
                tool_calls=[],
                input_tokens=20,
                output_tokens=10,
                model="m",
                provider="scripted",
            ),
        ]
    )

    mcp = _make_mcp_client(_mcp_handler)
    try:
        output = await kubernetes_agent.run(
            query="x", namespace="prod", service=None, llm=_router(scripted), mcp_k8s=mcp
        )
    finally:
        await mcp.aclose()

    assert output.succeeded is False
    assert output.notes and "Failed to produce structured summary" in output.notes
