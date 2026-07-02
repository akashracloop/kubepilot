"""search_logs — Datadog Logs Search → curated LogLine (logs capability)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mcp_datadog import client
from mcp_datadog.models import LogLine, LogQueryResult
from mcp_datadog.tools.base import Tool, register

_MAX_LINES = 200

_PARAMS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Datadog logs query, e.g. 'service:checkout status:error'",
        },
        "window_minutes": {"type": "integer", "description": "Lookback window (default 15)."},
        "limit": {"type": "integer", "description": f"Max lines (default 100, cap {_MAX_LINES})."},
    },
    "required": ["query"],
}


async def search_logs(query: str, window_minutes: int = 15, limit: int = 100) -> LogQueryResult:
    """Search Datadog logs and return curated LogLine rows.

    Maps Datadog's ``POST /api/v2/logs/events/search`` ``data[]`` into KubePilot's
    ``LogLine`` shape (identical to mcp-loki), newest-first, capped for the agent.
    """
    capped = max(1, min(limit, _MAX_LINES))
    end = datetime.now(UTC)
    start = end - timedelta(minutes=window_minutes)
    body = {
        "filter": {
            "query": query,
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
        "page": {"limit": capped},
        "sort": "-timestamp",
    }
    data = await client.post("/api/v2/logs/events/search", json=body)
    events = data.get("data", [])
    lines = [_map_event(e) for e in events[:capped]]
    return LogQueryResult(
        query=query,
        total_lines=len(lines),
        truncated=len(events) > capped,
        lines=lines,
    )


def _map_event(event: dict) -> LogLine:  # type: ignore[type-arg]
    attrs = event.get("attributes", {}) or {}
    message = attrs.get("message", "")
    ts_raw = attrs.get("timestamp")
    timestamp = _parse_ts(ts_raw)
    labels: dict[str, str] = {}
    for key in ("service", "status", "host"):
        val = attrs.get(key) or (attrs.get("attributes", {}) or {}).get(key)
        if val is not None:
            labels[key] = str(val)
    return LogLine(timestamp=timestamp, line=str(message), stream_labels=labels)


def _parse_ts(raw: object) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=UTC)
    return datetime.now(UTC)


register(
    Tool(
        name="search_logs",
        description="Search Datadog logs for a query/window; returns curated log lines.",
        parameters=_PARAMS,
        handler=search_logs,
    )
)
