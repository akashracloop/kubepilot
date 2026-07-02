"""Tool registry — every tool registers itself here on import."""

from mcp_ci.tools import commits, deployments, pipelines
from mcp_ci.tools.base import REGISTRY, Tool

_ = (commits, deployments, pipelines)  # force registration on import

__all__ = ["REGISTRY", "Tool"]
