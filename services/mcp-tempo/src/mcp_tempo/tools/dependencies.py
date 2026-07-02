"""service_dependency_map — upstream/downstream edges from Tempo's service graph."""

from __future__ import annotations

from typing import Any

from mcp_tempo import client
from mcp_tempo.models import DependencyEdge, DependencyMap
from mcp_tempo.tools.base import Tool, register


async def service_dependency_map(service: str, window_minutes: int = 60) -> DependencyMap:
    """Return the caller/callee edges touching a service.

    Backed by Tempo's service-graph metrics. Only edges where `service` is the
    caller or callee are returned, giving the agent its immediate neighbours
    (upstream callers and downstream dependencies).
    """
    params: dict[str, Any] = {"service": service, "lookback": f"{window_minutes}m"}
    data = await client.get("/api/metrics/service_graph", params=params)

    edges = [_to_edge(e) for e in data.get("edges", []) or []]
    edges = [e for e in edges if service in (e.caller, e.callee)]
    return DependencyMap(service=service, edges=edges)


def _to_edge(raw: dict[str, Any]) -> DependencyEdge:
    return DependencyEdge(
        caller=raw.get("caller", "") or "",
        callee=raw.get("callee", "") or "",
        call_count=int(raw.get("callCount", 0) or 0),
        error_count=int(raw.get("errorCount", 0) or 0),
        p99_ms=float(raw.get("p99Ms", 0.0) or 0.0),
    )


_SCHEMA = {
    "type": "object",
    "properties": {
        "service": {"type": "string"},
        "window_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1440,
            "default": 60,
            "description": "Aggregate the service graph over this many minutes.",
        },
    },
    "required": ["service"],
    "additionalProperties": False,
}


register(
    Tool(
        name="service_dependency_map",
        description=(
            "Return the upstream/downstream service-graph edges touching a service, each with "
            "call count, error count, and p99 latency. Use this to understand a service's "
            "immediate dependencies when localizing an incident."
        ),
        parameters=_SCHEMA,
        handler=service_dependency_map,
    )
)
