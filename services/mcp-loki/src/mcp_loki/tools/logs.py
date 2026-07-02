"""query_logs — raw LogQL query against Loki."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp_loki import client
from mcp_loki.models import LogLine, LogQueryResult
from mcp_loki.tools.base import Tool, register


async def query_logs(
    logql: str,
    start: str | None = None,
    end: str | None = None,
    window_minutes: int = 15,
    limit: int = 500,
    direction: str = "backward",
) -> LogQueryResult:
    """Run a LogQL query against Loki's query_range endpoint.

    Times are RFC3339; if omitted, the last `window_minutes` of logs are queried.
    `direction` is "backward" (newest first, default — what investigators want)
    or "forward".
    """
    end_dt = datetime.fromisoformat(end) if end else datetime.now(UTC)
    start_dt = (
        datetime.fromisoformat(start) if start else end_dt - timedelta(minutes=window_minutes)
    )

    params: dict[str, Any] = {
        "query": logql,
        "start": _to_nanos(start_dt),
        "end": _to_nanos(end_dt),
        "limit": int(limit),
        "direction": direction,
    }
    data = await client.get("/loki/api/v1/query_range", params=params)
    lines = _flatten_streams(data)
    return LogQueryResult(
        query=logql,
        total_lines=len(lines),
        truncated=len(lines) >= limit,
        lines=lines,
    )


def _to_nanos(dt: datetime) -> str:
    """Loki accepts Unix nanoseconds as a string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return str(int(dt.timestamp() * 1_000_000_000))


def _flatten_streams(data: dict[str, Any]) -> list[LogLine]:
    """Loki returns {"data": {"result": [{"stream": {...labels...}, "values": [[ns, line], ...]}]}}.

    We flatten into a single list of LogLine objects, preserving stream labels per line.
    """
    out: list[LogLine] = []
    result = data.get("data", {}).get("result", []) or []
    for stream in result:
        labels = stream.get("stream", {}) or {}
        for pair in stream.get("values", []) or []:
            ns, line = pair
            try:
                ts = datetime.fromtimestamp(int(ns) / 1_000_000_000, tz=UTC)
            except (ValueError, TypeError):
                continue
            out.append(LogLine(timestamp=ts, line=line, stream_labels=labels))
    # Newest first (LogQL backward direction already does this per stream, but
    # interleaved streams need an explicit sort).
    out.sort(key=lambda x: x.timestamp, reverse=True)
    return out


_SCHEMA = {
    "type": "object",
    "properties": {
        "logql": {
            "type": "string",
            "description": 'A LogQL expression, e.g. \'{namespace="prod",app="payment-service"}\'',
        },
        "start": {"type": ["string", "null"], "description": "RFC3339 start time"},
        "end": {"type": ["string", "null"], "description": "RFC3339 end time"},
        "window_minutes": {"type": "integer", "minimum": 1, "maximum": 1440, "default": 15},
        "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
        "direction": {"type": "string", "enum": ["backward", "forward"], "default": "backward"},
    },
    "required": ["logql"],
    "additionalProperties": False,
}


register(
    Tool(
        name="query_logs",
        description="Run a raw LogQL query against Loki. Use this when you already know what to filter on.",
        parameters=_SCHEMA,
        handler=query_logs,
    )
)
