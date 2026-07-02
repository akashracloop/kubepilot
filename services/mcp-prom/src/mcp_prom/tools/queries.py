"""query_metrics (instant) + query_range (time-series)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp_prom import client
from mcp_prom.models import (
    InstantQueryResult,
    MetricSeries,
    RangeQueryResult,
    _samples_from_values,
)
from mcp_prom.tools.base import Tool, register


async def query_metrics(promql: str, time: str | None = None) -> InstantQueryResult:
    """Run an instant PromQL query.

    `time` is an RFC3339 string. If omitted, Prometheus evaluates at "now".
    """
    params: dict[str, Any] = {"query": promql}
    if time:
        params["time"] = time

    data = await client.get("/api/v1/query", params=params)
    result = data["data"]
    result_type = result.get("resultType", "vector")
    series = _series_from_vector_or_matrix(result.get("result", []), result_type)
    return InstantQueryResult(query=promql, result_type=result_type, series=series)


async def query_range(
    promql: str,
    start: str | None = None,
    end: str | None = None,
    step_seconds: int = 30,
    window_minutes: int = 15,
) -> RangeQueryResult:
    """Run a range PromQL query.

    `start` and `end` are RFC3339 strings. If omitted, defaults to "now - window_minutes"
    through "now", with the agent's requested step.
    """
    end_dt = datetime.fromisoformat(end) if end else datetime.now(UTC)
    start_dt = (
        datetime.fromisoformat(start) if start else end_dt - timedelta(minutes=window_minutes)
    )

    params = {
        "query": promql,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "step": f"{step_seconds}s",
    }
    data = await client.get("/api/v1/query_range", params=params)

    result = data["data"]
    series = _series_from_vector_or_matrix(
        result.get("result", []), result.get("resultType", "matrix")
    )
    return RangeQueryResult(
        query=promql,
        start=start_dt,
        end=end_dt,
        step_seconds=step_seconds,
        series=series,
    )


def _series_from_vector_or_matrix(
    items: list[dict[str, Any]], result_type: str
) -> list[MetricSeries]:
    """Prometheus returns either `value: [ts, "x"]` (vector) or `values: [[ts, "x"], ...]` (matrix)."""
    series: list[MetricSeries] = []
    for item in items:
        labels = item.get("metric", {}) or {}
        if "values" in item:
            samples = _samples_from_values(item["values"])
        elif "value" in item:
            samples = _samples_from_values([item["value"]])
        else:
            samples = []
        series.append(MetricSeries(labels=labels, samples=samples))
    return series


_INSTANT_SCHEMA = {
    "type": "object",
    "properties": {
        "promql": {
            "type": "string",
            "description": "A PromQL expression, e.g. 'rate(http_requests_total[5m])'",
        },
        "time": {
            "type": ["string", "null"],
            "description": "RFC3339 evaluation time. Default: now.",
        },
    },
    "required": ["promql"],
    "additionalProperties": False,
}


_RANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "promql": {"type": "string"},
        "start": {"type": ["string", "null"], "description": "RFC3339 start time"},
        "end": {"type": ["string", "null"], "description": "RFC3339 end time"},
        "step_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 30},
        "window_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1440,
            "default": 15,
            "description": "Convenience: if start/end omitted, look back this many minutes.",
        },
    },
    "required": ["promql"],
    "additionalProperties": False,
}


register(
    Tool(
        name="query_metrics",
        description=(
            "Run an instant PromQL query. Returns one sample per series. "
            "Use this for current values (e.g. 'how much memory is payment-service using right now?')."
        ),
        parameters=_INSTANT_SCHEMA,
        handler=query_metrics,
    )
)

register(
    Tool(
        name="query_range",
        description=(
            "Run a range PromQL query to get a time-series. Use this for trend analysis "
            "(e.g. 'how has memory usage changed in the last 15 minutes?')."
        ),
        parameters=_RANGE_SCHEMA,
        handler=query_range,
    )
)
