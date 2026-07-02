"""Curated capability shapes returned by the Datadog adapter.

These deliberately mirror the Prometheus (``MetricSeries``/``Sample``) and Loki
(``LogLine``) response models so the ``metrics`` and ``logs`` capabilities are a
config-only swap. The adapter maps Datadog → **these** shapes (never the reverse),
so the orchestrator sees one curated contract regardless of backend.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Sample(BaseModel):
    """A single (timestamp, value) sample — matches mcp-prom.Sample."""

    timestamp: datetime
    value: float


class MetricSeries(BaseModel):
    """A labeled time-series — matches mcp-prom.MetricSeries."""

    labels: dict[str, str] = Field(default_factory=dict)
    samples: list[Sample] = Field(default_factory=list)


class TimeseriesResult(BaseModel):
    query: str
    start: datetime
    end: datetime
    series: list[MetricSeries] = Field(default_factory=list)


class LogLine(BaseModel):
    """A single log event — matches mcp-loki.LogLine."""

    timestamp: datetime
    line: str
    stream_labels: dict[str, str] = Field(default_factory=dict)


class LogQueryResult(BaseModel):
    query: str
    total_lines: int = 0
    truncated: bool = False
    lines: list[LogLine] = Field(default_factory=list)
