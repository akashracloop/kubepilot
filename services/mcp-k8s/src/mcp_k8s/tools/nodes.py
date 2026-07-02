"""get_nodes — cluster node health and capacity."""

from __future__ import annotations

from typing import Any

from mcp_k8s.client import get_core_v1
from mcp_k8s.models import NodeSummary
from mcp_k8s.tools.base import Tool, register, to_thread


async def get_nodes() -> list[NodeSummary]:
    api = get_core_v1()
    raw = await to_thread(api.list_node)
    return [_summarize_node(n) for n in raw.items]


def _summarize_node(node: Any) -> NodeSummary:
    status = node.status
    info = status.node_info if status and status.node_info else None
    conditions = [c.to_dict() for c in (status.conditions or [])] if status else []
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)

    taints = [t.to_dict() for t in (node.spec.taints or [])] if node.spec else []
    schedulable = not (node.spec.unschedulable if node.spec else False)

    return NodeSummary(
        name=node.metadata.name,
        ready=ready,
        schedulable=schedulable,
        kubelet_version=getattr(info, "kubelet_version", None) if info else None,
        os=getattr(info, "operating_system", None) if info else None,
        architecture=getattr(info, "architecture", None) if info else None,
        capacity={k: str(v) for k, v in (status.capacity or {}).items()} if status else {},
        allocatable=({k: str(v) for k, v in (status.allocatable or {}).items()} if status else {}),
        conditions=conditions,
        taints=taints,
    )


_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


register(
    Tool(
        name="get_nodes",
        description="List all cluster nodes with readiness, schedulability, capacity, and taints.",
        parameters=_SCHEMA,
        handler=get_nodes,
    )
)
