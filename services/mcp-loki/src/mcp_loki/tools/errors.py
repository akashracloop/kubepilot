"""search_errors — convenience wrapper to find error-level logs for a service."""

from __future__ import annotations

from mcp_loki.models import LogQueryResult
from mcp_loki.tools.base import Tool, register
from mcp_loki.tools.logs import query_logs


async def search_errors(
    namespace: str,
    service: str | None = None,
    window_minutes: int = 15,
    limit: int = 500,
) -> LogQueryResult:
    """Find ERROR-level (and above) log lines for a service.

    Builds a sensible LogQL out of namespace/service filters + a severity regex.
    Matches against common log-format conventions: `level=error`, `"level":"error"`,
    bracketed `[ERROR]`, leading `ERROR:`, and so on (case-insensitive).
    """
    selector_parts = [f'namespace="{namespace}"']
    if service:
        selector_parts.append(f'app="{service}"')
    selector = "{" + ",".join(selector_parts) + "}"

    # Conservative severity filter — case-insensitive, matches common shapes.
    severity_regex = (
        r'(?i)(?:^|[\s\[\"\']|level=|severity=|"level":\s*\"|"severity":\s*\")'
        r"(ERROR|FATAL|CRITICAL|PANIC|SEVERE)"
        r"(?:[\s\]\"\']|$)"
    )

    logql = f"{selector} |~ `{severity_regex}`"
    return await query_logs(logql=logql, window_minutes=window_minutes, limit=limit)


_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string"},
        "service": {"type": ["string", "null"], "description": "Service / app label value"},
        "window_minutes": {"type": "integer", "minimum": 1, "maximum": 1440, "default": 15},
        "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
    },
    "required": ["namespace"],
    "additionalProperties": False,
}


register(
    Tool(
        name="search_errors",
        description=(
            "Convenience wrapper: find error/fatal/critical level log lines for a service "
            "in a namespace. Use this first when investigating; fall back to query_logs for "
            "custom LogQL."
        ),
        parameters=_SCHEMA,
        handler=search_errors,
    )
)
