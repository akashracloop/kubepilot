"""Tool registry — all tools register on import."""

from mcp_loki.tools import errors, exceptions, logs
from mcp_loki.tools.base import REGISTRY, Tool

_ = (errors, exceptions, logs)

__all__ = ["REGISTRY", "Tool"]
