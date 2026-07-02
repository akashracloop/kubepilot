"""query_metrics — Datadog timeseries → curated MetricSeries (metrics capability)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mcp_datadog import client
from mcp_datadog.models import MetricSeries, Sample, TimeseriesResult
from mcp_datadog.tools.base import Tool, register

_PARAMS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Datadog metrics query, e.g. 'avg:system.mem.used{service:checkout}'",
        },
        "window_minutes": {"type": "integer", "description": "Lookback window (default 15)."},
    },
    "required": ["query"],
}


async def query_metrics(query: str, window_minutes: int = 15) -> TimeseriesResult:
    """Run a Datadog timeseries query and return curated MetricSeries.

    Maps Datadog's ``/api/v1/query`` ``series[].pointlist`` ([[ms, value], ...])
    into KubePilot's ``MetricSeries``/``Sample`` shape (identical to mcp-prom).
    """
    end = datetime.now(UTC)
    start = end - timedelta(minutes=window_minutes)
    data = await client.get(
        "/api/v1/query",
        params={
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
            "query": query,
        },
    )
    series = [_map_series(s) for s in data.get("series", [])]
    return TimeseriesResult(query=query, start=start, end=end, series=series)


def _map_series(s: dict) -> MetricSeries:  # type: ignore[type-arg]
    labels: dict[str, str] = {}
    if s.get("scope"):
        # Datadog scope is "k:v,k2:v2"; split into labels.
        for pair in str(s["scope"]).split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                labels[k.strip()] = v.strip()
    if s.get("metric"):
        labels.setdefault("__name__", str(s["metric"]))

    samples: list[Sample] = []
    for point in s.get("pointlist", []):
        if not point or len(point) < 2 or point[1] is None:
            continue
        ts_ms, value = point[0], point[1]
        samples.append(
            Sample(
                timestamp=datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=UTC), value=float(value)
            )
        )
    return MetricSeries(labels=labels, samples=samples)


register(
    Tool(
        name="query_metrics",
        description="Run a Datadog metrics timeseries query; returns curated labeled series.",
        parameters=_PARAMS,
        handler=query_metrics,
    )
)
