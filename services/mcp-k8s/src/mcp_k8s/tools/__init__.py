"""Tool registry — every tool registers itself here on import."""

from mcp_k8s.tools import (
    configmaps,
    deployments,
    events,
    nodes,
    pods,
    pvcs,
    services,
)
from mcp_k8s.tools.base import REGISTRY, Tool

# Force registration on import.
_ = (configmaps, deployments, events, nodes, pods, pvcs, services)

__all__ = ["REGISTRY", "Tool"]
