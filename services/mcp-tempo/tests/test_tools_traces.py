"""Tests for query_traces + get_trace + find_failed_spans."""

from __future__ import annotations

import pytest
from mcp_tempo.tools.traces import find_failed_spans, get_trace, query_traces


@pytest.mark.asyncio
async def test_query_traces_summarizes_and_flags_slow_downstream(tempo) -> None:  # type: ignore[no-untyped-def]
    # Acceptance: a trace whose slow span is a downstream dependency should have
    # slowest_span.service point at that dependency (PHASE_2_PLAN §5.1).
    tempo.set_response(
        {
            "traces": [
                {
                    "traceID": "abc123",
                    "rootServiceName": "checkout",
                    "rootDurationMs": 240,
                    "spans": [
                        {
                            "spanID": "s1",
                            "service": "checkout",
                            "name": "POST /checkout",
                            "durationNanos": 150_000_000,
                            "status": "ok",
                        },
                        {
                            "spanID": "s2",
                            "service": "payments",
                            "name": "charge",
                            "durationNanos": 180_000_000,
                            "status": "error",
                        },
                    ],
                }
            ]
        }
    )

    summaries = await query_traces("checkout")

    assert len(summaries) == 1
    s = summaries[0]
    assert s.trace_id == "abc123"
    assert s.root_service == "checkout"
    assert s.root_duration_ms == pytest.approx(240)
    assert s.span_count == 2
    assert s.error_count == 1
    assert s.slowest_span is not None
    assert s.slowest_span.service == "payments"
    assert s.slowest_span.status == "error"
    assert s.slowest_span.duration_ms == pytest.approx(180)
    assert tempo.calls[0]["path"] == "/api/search"
    assert tempo.calls[0]["params"]["service.name"] == "checkout"


@pytest.mark.asyncio
async def test_query_traces_defaults_window_when_no_start(tempo) -> None:  # type: ignore[no-untyped-def]
    tempo.set_response({"traces": []})

    await query_traces("checkout")

    params = tempo.calls[0]["params"]
    assert "start" in params and "end" in params


@pytest.mark.asyncio
async def test_get_trace_returns_all_spans(tempo) -> None:  # type: ignore[no-untyped-def]
    tempo.set_response(
        {
            "trace": {
                "traceID": "abc123",
                "rootDurationMs": 240,
                "spans": [
                    {
                        "spanID": "s1",
                        "service": "checkout",
                        "name": "POST /checkout",
                        "durationNanos": 240_000_000,
                        "status": "ok",
                    },
                    {
                        "spanID": "s2",
                        "service": "payments",
                        "name": "charge",
                        "durationNanos": 180_000_000,
                        "status": "error",
                    },
                ],
            }
        }
    )

    detail = await get_trace("abc123")

    assert detail.trace_id == "abc123"
    assert detail.root_duration_ms == pytest.approx(240)
    assert len(detail.spans) == 2
    assert detail.error_count == 1
    assert detail.spans[1].service == "payments"
    assert detail.spans[1].status == "error"
    assert tempo.calls[0]["path"] == "/api/traces/abc123"


@pytest.mark.asyncio
async def test_find_failed_spans_filters_to_errors(tempo) -> None:  # type: ignore[no-untyped-def]
    tempo.set_response(
        {
            "traces": [
                {
                    "traceID": "t1",
                    "spans": [
                        {
                            "spanID": "a",
                            "service": "payments",
                            "name": "charge",
                            "durationNanos": 50_000_000,
                            "status": "error",
                        },
                        {
                            "spanID": "b",
                            "service": "payments",
                            "name": "validate",
                            "durationNanos": 10_000_000,
                            "status": "ok",
                        },
                    ],
                },
                {
                    "traceID": "t2",
                    "spans": [
                        {
                            "spanID": "c",
                            "service": "payments",
                            "name": "refund",
                            "durationNanos": 70_000_000,
                            "status": "error",
                        }
                    ],
                },
            ]
        }
    )

    result = await find_failed_spans("payments", window_minutes=30)

    assert result.service == "payments"
    assert result.window_minutes == 30
    assert {s.span_id for s in result.spans} == {"a", "c"}
    assert all(s.status == "error" for s in result.spans)
    params = tempo.calls[0]["params"]
    assert params["tags"] == "status=error"
    assert "start" in params and "end" in params
