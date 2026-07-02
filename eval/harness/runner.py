"""Drive the full investigation graph against a scenario's canned MCP fixture.

The heavy lifting is done by the orchestrator's own test helpers:
  - ``build_mcp_client(handler, server_name)`` wraps an ``httpx.MockTransport``
    so an MCP server is faked entirely in-process.
  - ``build_graph(deps).ainvoke(...)`` runs START → supervisor → (k8s ∥ metrics ∥
    logs) → rca → recommendation → finalize exactly as in production.

For each scenario we build one MockTransport handler per MCP server. The handler
answers:
  - ``GET  /mcp/tools``  → tool descriptors for the tools present in the fixture.
  - ``POST /mcp/invoke`` → ``{"tool": <name>, "result": <canned payload>}``.

The LLM is supplied by the caller: a real provider router on the live accuracy
path (``run_eval.py``) or a ``ScriptedLLM`` router in the deterministic
self-test. The runner is LLM-agnostic — it only wires transport + graph.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.state import InvestigationState
from kubepilot_orch.testing import build_mcp_client

from eval.harness.loader import MCP_SERVERS, Scenario

# ---------------------------------------------------------------------------
# Tool descriptors. The live LLM needs a plausible JSON-Schema for each tool so
# it can decide which to call; the canned result is returned regardless of the
# arguments the model passes, so permissive schemas are fine.
# ---------------------------------------------------------------------------

_NS = {"namespace": {"type": "string"}}
_NS_NAME = {"namespace": {"type": "string"}, "name": {"type": "string"}}
_PROMQL_RANGE = {
    "promql": {"type": "string"},
    "start": {"type": "string"},
    "end": {"type": "string"},
    "step": {"type": "string"},
}
_SERVICE_WINDOW = {"service": {"type": "string"}, "time_range": {"type": "string"}}

_TOOL_SCHEMAS: dict[str, tuple[str, dict[str, Any]]] = {
    # mcp-k8s
    "list_pods": ("List pods in a namespace, optionally filtered by label selector.", _NS),
    "describe_pod": ("Describe a pod: spec + status + recent events.", _NS_NAME),
    "get_events": ("List recent events in a namespace.", _NS),
    "get_nodes": ("List cluster nodes and their conditions.", {}),
    "get_deployments": ("List deployments in a namespace.", _NS),
    "get_services": ("List services (and their endpoints/selectors) in a namespace.", _NS),
    "get_pvcs": ("List PersistentVolumeClaims in a namespace.", _NS),
    "get_configmap": ("Read a ConfigMap by name.", _NS_NAME),
    # mcp-prom
    "query_metrics": ("Run an instant PromQL query.", {"promql": {"type": "string"}}),
    "query_range": ("Run a range PromQL query.", _PROMQL_RANGE),
    "list_targets": ("List Prometheus scrape targets.", {}),
    "query_alerts": ("List currently firing alerts.", {}),
    # mcp-loki
    "query_logs": ("Run a LogQL query over a time range.", {"logql": {"type": "string"}}),
    "search_errors": ("Convenience wrapper: find error lines for a service.", _SERVICE_WINDOW),
    "search_exceptions": (
        "Workload-agnostic exception/stack-trace detection (Java/Python/Node/Go/generic).",
        _SERVICE_WINDOW,
    ),
}


def _descriptor(tool: str) -> dict[str, Any]:
    description, props = _TOOL_SCHEMAS.get(
        tool, (f"Fixture tool {tool}.", {"namespace": {"type": "string"}})
    )
    return {
        "name": tool,
        "description": description,
        "parameters": {"type": "object", "properties": props},
    }


def _make_handler(server_fixture: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    """Build an ``httpx.MockTransport`` handler serving one server's fixture."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(
                200, json={"tools": [_descriptor(name) for name in server_fixture]}
            )
        if request.url.path == "/mcp/health":
            return httpx.Response(200, json={"status": "ok"})
        # POST /mcp/invoke
        body = json.loads(request.content.decode())
        tool = body["tool"]
        # Unknown tool (e.g. the live LLM asked for one not staged) → empty result
        # rather than a 5xx, so the agent degrades to "no signal" instead of erroring.
        result = server_fixture.get(tool, {"note": f"no fixture staged for tool {tool!r}"})
        return httpx.Response(200, json={"tool": tool, "result": result})

    return handler


def build_deps(scenario: Scenario, llm: LLMRouter) -> AgentDeps:
    """Assemble ``AgentDeps`` with one mocked MCP client per server."""
    clients = {
        server: build_mcp_client(_make_handler(scenario.server_fixture(server)), server_name=server)
        for server in MCP_SERVERS
    }
    return AgentDeps(
        llm=llm,
        mcp_k8s=clients["mcp-k8s"],
        mcp_prom=clients["mcp-prom"],
        mcp_loki=clients["mcp-loki"],
    )


async def run_scenario(scenario: Scenario, llm: LLMRouter) -> InvestigationState:
    """Run the investigation graph for one scenario and return the final state.

    The returned ``InvestigationState`` carries the ``rca`` report and merged
    ``evidence`` the scorer grades. MCP clients are always closed, even on error.
    """
    deps = build_deps(scenario, llm)
    try:
        graph = build_graph(deps)
        final: dict[str, Any] = await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": scenario.query,
                "namespace": scenario.namespace,
                "service": scenario.service,
                "started_at": datetime.now(UTC),
            }
        )
    finally:
        await deps.mcp_k8s.aclose()
        await deps.mcp_prom.aclose()
        await deps.mcp_loki.aclose()

    return InvestigationState.model_validate(final)
