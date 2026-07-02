"""query_traces + get_trace + find_failed_spans — curated Tempo span views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp_tempo import client
from mcp_tempo.models import (
    FailedSpansResult,
    SpanRef,
    TraceDetail,
    TraceSummary,
    _spans_from_raw,
)
from mcp_tempo.tools.base import Tool, register


async def query_traces(
    service: str,
    start: str | None = None,
    end: str | None = None,
    tags: dict[str, str] | None = None,
    limit: int = 20,
) -> list[TraceSummary]:
    """Search recent traces for a service.

    `start`/`end` are RFC3339 strings. If omitted, defaults to the last hour.
    `tags` narrows the search (e.g. {"http.status_code": "500"}).
    """
    end_dt = datetime.fromisoformat(end) if end else datetime.now(UTC)
    start_dt = datetime.fromisoformat(start) if start else end_dt - timedelta(hours=1)

    params: dict[str, Any] = {
        "service.name": service,
        "start": int(start_dt.timestamp()),
        "end": int(end_dt.timestamp()),
        "limit": limit,
    }
    if tags:
        params["tags"] = " ".join(f"{k}={v}" for k, v in tags.items())

    data = await client.get("/api/search", params=params)
    return [_to_summary(t) for t in data.get("traces", []) or []]


async def get_trace(trace_id: str) -> TraceDetail:
    """Fetch every span of a single trace by id."""
    data = await client.get(f"/api/traces/{trace_id}")
    trace = data.get("trace", data) or {}
    spans = _spans_from_raw(trace.get("spans", []) or [])
    return TraceDetail(
        trace_id=str(trace.get("traceID", trace_id)),
        spans=spans,
        root_duration_ms=float(trace.get("rootDurationMs", 0) or 0),
        error_count=sum(1 for s in spans if s.status == "error"),
    )


async def find_failed_spans(service: str, window_minutes: int = 15) -> FailedSpansResult:
    """Return error/abnormal spans for a service over a recent window."""
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(minutes=window_minutes)

    params: dict[str, Any] = {
        "service.name": service,
        "start": int(start_dt.timestamp()),
        "end": int(end_dt.timestamp()),
        "tags": "status=error",
    }
    data = await client.get("/api/search", params=params)

    failed: list[SpanRef] = []
    for trace in data.get("traces", []) or []:
        failed.extend(
            s for s in _spans_from_raw(trace.get("spans", []) or []) if s.status == "error"
        )
    return FailedSpansResult(service=service, window_minutes=window_minutes, spans=failed)


def _to_summary(raw: dict[str, Any]) -> TraceSummary:
    """Collapse a search hit's span set into a single-line summary."""
    spans = _spans_from_raw(raw.get("spans", []) or [])
    slowest = max(spans, key=lambda s: s.duration_ms, default=None)
    root_duration = raw.get("rootDurationMs")
    if root_duration is None:
        root_duration = slowest.duration_ms if slowest else 0.0
    return TraceSummary(
        trace_id=str(raw.get("traceID", "")),
        root_service=raw.get("rootServiceName", "") or "",
        root_duration_ms=float(root_duration or 0),
        span_count=len(spans),
        error_count=sum(1 for s in spans if s.status == "error"),
        slowest_span=slowest,
    )


_QUERY_TRACES_SCHEMA = {
    "type": "object",
    "properties": {
        "service": {"type": "string", "description": "Service name to search traces for."},
        "start": {
            "type": ["string", "null"],
            "description": "RFC3339 start time. Default: 1h ago.",
        },
        "end": {"type": ["string", "null"], "description": "RFC3339 end time. Default: now."},
        "tags": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"},
            "description": 'Optional tag filters, e.g. {"http.status_code": "500"}.',
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
    },
    "required": ["service"],
    "additionalProperties": False,
}


_GET_TRACE_SCHEMA = {
    "type": "object",
    "properties": {
        "trace_id": {"type": "string", "description": "The trace ID to fetch."},
    },
    "required": ["trace_id"],
    "additionalProperties": False,
}


_FAILED_SPANS_SCHEMA = {
    "type": "object",
    "properties": {
        "service": {"type": "string"},
        "window_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1440,
            "default": 15,
            "description": "Look back this many minutes for error spans.",
        },
    },
    "required": ["service"],
    "additionalProperties": False,
}


register(
    Tool(
        name="query_traces",
        description=(
            "Search recent traces for a service. Returns one curated TraceSummary per trace "
            "(root duration, span/error counts, slowest span) rather than raw spans. "
            "Use this to find slow or failing requests (e.g. 'show recent traces for checkout')."
        ),
        parameters=_QUERY_TRACES_SCHEMA,
        handler=query_traces,
    )
)

register(
    Tool(
        name="get_trace",
        description=(
            "Fetch a single trace by ID and return all of its spans with timings and status. "
            "Use this to drill into a specific trace surfaced by query_traces."
        ),
        parameters=_GET_TRACE_SCHEMA,
        handler=get_trace,
    )
)

register(
    Tool(
        name="find_failed_spans",
        description=(
            "Return the error/abnormal spans for a service over a recent window. "
            "Use this to pinpoint which operations are failing during an incident."
        ),
        parameters=_FAILED_SPANS_SCHEMA,
        handler=find_failed_spans,
    )
)
