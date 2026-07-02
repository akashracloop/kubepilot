"""Pydantic response models for Tempo tools.

We curate Tempo's verbose span trees into compact summaries — an agent reasons
about a ``TraceSummary`` (root duration, error count, slowest span) far more
cheaply than about the raw OTLP span batches. Same token-efficiency argument as
mcp-k8s's ``PodSummary`` (see docs/ARCHITECTURE.md §3.3.1).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Tempo/OTLP spell span status a few different ways; collapse them onto our
# three-value enum so agents never have to reason about the raw code constants.
_STATUS_ALIASES = {
    "ok": "ok",
    "status_code_ok": "ok",
    "error": "error",
    "status_code_error": "error",
    "unset": "unset",
    "status_code_unset": "unset",
}


class SpanRef(BaseModel):
    """A single span, flattened to the fields useful for diagnosis."""

    span_id: str
    service: str
    name: str
    duration_ms: float
    status: str  # "ok" | "error" | "unset"


class TraceSummary(BaseModel):
    """One curated line per trace (returned by query_traces)."""

    trace_id: str
    root_service: str
    root_duration_ms: float
    span_count: int
    error_count: int
    slowest_span: SpanRef | None = None


class TraceDetail(BaseModel):
    """All spans of a single trace (returned by get_trace)."""

    trace_id: str
    spans: list[SpanRef] = Field(default_factory=list)
    root_duration_ms: float
    error_count: int


class DependencyEdge(BaseModel):
    """One caller→callee edge in a service graph."""

    caller: str
    callee: str
    call_count: int
    error_count: int
    p99_ms: float


class DependencyMap(BaseModel):
    service: str
    edges: list[DependencyEdge] = Field(default_factory=list)


class FailedSpansResult(BaseModel):
    service: str
    window_minutes: int
    spans: list[SpanRef] = Field(default_factory=list)


def _normalize_status(raw: Any) -> str:
    """Map a Tempo/OTLP status spelling onto our three-value enum."""
    if raw is None:
        return "unset"
    return _STATUS_ALIASES.get(str(raw).strip().lower(), "unset")


def _duration_ms(raw: dict[str, Any]) -> float:
    """Tempo reports durations in nanoseconds; some search views expose ms directly."""
    if raw.get("durationMs") is not None:
        return float(raw["durationMs"])
    return float(raw.get("durationNanos", 0) or 0) / 1_000_000.0


def _span_ref(raw: dict[str, Any]) -> SpanRef:
    return SpanRef(
        span_id=str(raw.get("spanID", "")),
        service=raw.get("service", "") or "",
        name=raw.get("name", "") or "",
        duration_ms=_duration_ms(raw),
        status=_normalize_status(raw.get("status")),
    )


def _spans_from_raw(items: list[dict[str, Any]]) -> list[SpanRef]:
    return [_span_ref(s) for s in items]
