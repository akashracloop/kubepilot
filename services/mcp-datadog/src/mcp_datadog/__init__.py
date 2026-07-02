"""Datadog observability-adapter MCP server (Phase 3 reference).

Implements the KubePilot MCP REST contract (``/mcp/tools``, ``/mcp/invoke``,
``/mcp/health``) over the Datadog API, mapping Datadog responses into KubePilot's
**curated capability shapes** (the same MetricSeries / LogLine models the
Prometheus and Loki servers return). A Datadog shop points the ``metrics`` and
``logs`` capabilities at this server — config-only, no agent change.

Read-only by construction: only query/search tools are exposed; nothing mutates
Datadog or the cluster. The read/write bright line (Phase 4) is untouched.
"""

from __future__ import annotations

__version__ = "0.1.0-dev"
