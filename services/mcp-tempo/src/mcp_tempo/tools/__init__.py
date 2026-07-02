"""Tool registry — every tool registers itself here on import."""

from mcp_tempo.tools import dependencies, traces
from mcp_tempo.tools.base import REGISTRY, Tool

_ = (dependencies, traces)  # force registration on import

__all__ = ["REGISTRY", "Tool"]
