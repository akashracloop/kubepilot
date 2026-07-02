"""Importing this package registers all Datadog tools with the REGISTRY."""

from __future__ import annotations

from mcp_datadog.tools import logs, metrics  # noqa: F401  (import for side-effect: registration)
from mcp_datadog.tools.base import REGISTRY

__all__ = ["REGISTRY"]
