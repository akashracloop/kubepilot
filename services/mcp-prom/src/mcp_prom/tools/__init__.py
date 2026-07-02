"""Tool registry — every tool registers itself here on import."""

from mcp_prom.tools import alerts, queries, targets
from mcp_prom.tools.base import REGISTRY, Tool

_ = (alerts, queries, targets)  # force registration on import

__all__ = ["REGISTRY", "Tool"]
