"""Pydantic response models for Prometheus tools.

We normalize Prometheus's slightly awkward array-of-pairs format into something
agents can reason about with less ceremony.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Sample(BaseModel):
    """A single (timestamp, value) sample."""

    timestamp: datetime
    value: float


class MetricSeries(BaseModel):
    """A labeled time-series — used by both instant and range queries.

    For instant queries, ``samples`` has exactly one entry.
    For range queries, samples are sorted ascending by timestamp.
    """

    labels: dict[str, str] = Field(default_factory=dict)
    samples: list[Sample] = Field(default_factory=list)


class InstantQueryResult(BaseModel):
    query: str
    result_type: str  # "vector" | "scalar" | "matrix" | "string"
    series: list[MetricSeries] = Field(default_factory=list)


class RangeQueryResult(BaseModel):
    query: str
    start: datetime
    end: datetime
    step_seconds: int
    series: list[MetricSeries] = Field(default_factory=list)


class Target(BaseModel):
    """A Prometheus scrape target (subset of fields useful for diagnosis)."""

    job: str
    instance: str
    health: str  # "up" | "down" | "unknown"
    last_error: str | None = None
    last_scrape_seconds_ago: float | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class TargetsView(BaseModel):
    active: list[Target] = Field(default_factory=list)
    dropped_count: int = 0


class Alert(BaseModel):
    name: str
    state: str  # "firing" | "pending" | "inactive"
    severity: str | None = None
    summary: str | None = None
    description: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    active_since: datetime | None = None


class AlertsView(BaseModel):
    alerts: list[Alert] = Field(default_factory=list)


def _samples_from_values(values: list[list[Any]]) -> list[Sample]:
    out: list[Sample] = []
    for pair in values:
        ts, raw_val = pair
        try:
            v = float(raw_val)
        except (TypeError, ValueError):
            continue  # skip non-numeric samples
        out.append(Sample(timestamp=datetime.fromtimestamp(float(ts)), value=v))
    return out
