"""Metrics agent — memory-spike-into-OOM scenario."""

from __future__ import annotations

import json

import httpx
import pytest
from kubepilot_orch.agents import metrics_agent
from kubepilot_orch.agents.metrics_agent import AGENT_NAME
from kubepilot_orch.state import AgentOutput, Evidence, Severity
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)

_TOOL_DESCRIPTORS = {
    "tools": [
        {
            "name": "query_metrics",
            "description": "Instant PromQL query.",
            "parameters": {
                "type": "object",
                "properties": {"promql": {"type": "string"}},
                "required": ["promql"],
            },
        },
        {
            "name": "query_range",
            "description": "Range PromQL query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "promql": {"type": "string"},
                    "window_minutes": {"type": "integer"},
                },
                "required": ["promql"],
            },
        },
        {
            "name": "query_alerts",
            "description": "Firing alerts.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
}


# Memory rising from 256 MiB baseline → 1024 MiB peak over 15 minutes — classic pre-OOM signal.
_MEMORY_RANGE_RESULT = {
    "query": "container_memory_working_set_bytes",
    "result_type": "matrix",
    "series": [
        {
            "labels": {"pod": "payment-service-0", "container": "app"},
            "samples": [
                {"timestamp": "2026-06-23T09:50:00Z", "value": 268_435_456.0},
                {"timestamp": "2026-06-23T09:55:00Z", "value": 402_653_184.0},
                {"timestamp": "2026-06-23T10:00:00Z", "value": 671_088_640.0},
                {"timestamp": "2026-06-23T10:05:00Z", "value": 1_073_741_824.0},
            ],
        }
    ],
}

_ERROR_RATE_RESULT = {
    "query": 'rate(http_requests_total{status=~"5.."}[5m])',
    "result_type": "vector",
    "series": [
        {
            "labels": {"service": "payment-service"},
            "samples": [{"timestamp": "2026-06-23T10:05:00Z", "value": 47.2}],
        }
    ],
}


def _prom_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/mcp/tools":
        return httpx.Response(200, json=_TOOL_DESCRIPTORS)
    if request.url.path == "/mcp/invoke":
        body = json.loads(request.content.decode())
        tool = body["tool"]
        if tool == "query_range":
            return httpx.Response(200, json={"tool": tool, "result": _MEMORY_RANGE_RESULT})
        if tool == "query_metrics":
            return httpx.Response(200, json={"tool": tool, "result": _ERROR_RATE_RESULT})
        if tool == "query_alerts":
            return httpx.Response(200, json={"tool": tool, "result": {"alerts": []}})
        return httpx.Response(404, json={"detail": f"Unknown tool: {tool}"})
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_metrics_agent_detects_memory_spike_into_oom() -> None:
    """Range query shows memory growing 4x in 15 min → evidence of resource saturation."""
    expected = AgentOutput(
        agent_name=AGENT_NAME,
        succeeded=True,
        evidence=[
            Evidence(
                source_agent=AGENT_NAME,
                kind="resource_saturation",
                summary=(
                    "payment-service-0 memory grew from 256 MiB baseline to 1024 MiB "
                    "(4x) over 15 minutes — consistent with pre-OOM pressure."
                ),
                detail={
                    "metric": "container_memory_working_set_bytes",
                    "pod": "payment-service-0",
                    "baseline_bytes": 268435456,
                    "peak_bytes": 1073741824,
                    "growth_multiple": 4.0,
                    "window_minutes": 15,
                },
                severity=Severity.CRITICAL,
                collected_at="2026-06-23T10:08:30Z",
            ),
            Evidence(
                source_agent=AGENT_NAME,
                kind="error_rate",
                summary="HTTP 5xx rate is 47.2 rps — elevated.",
                detail={
                    "metric": 'rate(http_requests_total{status=~"5.."}[5m])',
                    "value": 47.2,
                },
                severity=Severity.ERROR,
                collected_at="2026-06-23T10:08:30Z",
            ),
        ],
        notes=("Memory saturation + elevated 5xx rate. Metrics-level only; root cause TBD by RCA."),
    )

    scripted = ScriptedLLM(
        responses=[
            # Tool 1: range query for memory trend
            llm_tool_call(
                "query_range",
                {
                    "promql": (
                        'container_memory_working_set_bytes{namespace="prod",'
                        'pod=~"payment-service.*"}'
                    ),
                    "window_minutes": 15,
                },
                call_id="c1",
            ),
            # Tool 2: instant query for error rate
            llm_tool_call(
                "query_metrics",
                {"promql": 'rate(http_requests_total{status=~"5.."}[5m])'},
                call_id="c2",
            ),
            # Tool 3: check existing alerts
            llm_tool_call("query_alerts", {}, call_id="c3"),
            # Done.
            llm_text("Investigation complete."),
            # Phase 2: structured summary
            llm_text(expected.model_dump_json()),
        ]
    )

    mcp = build_mcp_client(_prom_handler, server_name="mcp-prom")
    try:
        output = await metrics_agent.run(
            query="why is payment-service failing?",
            namespace="prod",
            service="payment-service",
            llm=build_router(scripted),
            mcp_prom=mcp,
        )
    finally:
        await mcp.aclose()

    assert output.agent_name == AGENT_NAME
    assert output.succeeded is True
    assert output.tokens_used > 0
    assert len(output.evidence) >= 2

    summaries = " ".join(e.summary.lower() for e in output.evidence)
    assert "memory" in summaries
    assert any(e.severity in {Severity.CRITICAL, Severity.ERROR} for e in output.evidence)

    # The agent shouldn't speculate on root cause in its notes.
    if output.notes:
        assert "tbd" in output.notes.lower() or "rca" in output.notes.lower()
