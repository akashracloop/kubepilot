"""Tests for query_metrics + query_range."""

from __future__ import annotations

import pytest
from mcp_prom.tools.queries import query_metrics, query_range


@pytest.mark.asyncio
async def test_query_metrics_instant_vector(prom) -> None:  # type: ignore[no-untyped-def]
    prom.set_response(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {"__name__": "container_memory_usage_bytes", "pod": "payment-0"},
                        "value": [1718710000.0, "536870912"],
                    },
                    {
                        "metric": {"__name__": "container_memory_usage_bytes", "pod": "payment-1"},
                        "value": [1718710000.0, "734003200"],
                    },
                ],
            },
        }
    )

    result = await query_metrics("container_memory_usage_bytes")

    assert result.query == "container_memory_usage_bytes"
    assert result.result_type == "vector"
    assert len(result.series) == 2
    assert result.series[0].labels["pod"] == "payment-0"
    assert result.series[0].samples[0].value == pytest.approx(536870912)
    assert prom.calls[0]["path"] == "/api/v1/query"
    assert prom.calls[0]["params"]["query"] == "container_memory_usage_bytes"


@pytest.mark.asyncio
async def test_query_range_returns_matrix(prom) -> None:  # type: ignore[no-untyped-def]
    prom.set_response(
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"pod": "payment-0"},
                        "values": [
                            [1718710000.0, "100"],
                            [1718710030.0, "200"],
                            [1718710060.0, "300"],
                        ],
                    }
                ],
            },
        }
    )

    result = await query_range(
        "rate(http_requests_total[5m])",
        start="2026-06-18T10:00:00",
        end="2026-06-18T10:15:00",
        step_seconds=30,
    )

    assert len(result.series) == 1
    assert len(result.series[0].samples) == 3
    assert [s.value for s in result.series[0].samples] == [100.0, 200.0, 300.0]
    assert prom.calls[0]["params"]["step"] == "30s"


@pytest.mark.asyncio
async def test_query_range_defaults_to_window_when_no_start(prom) -> None:  # type: ignore[no-untyped-def]
    prom.set_response({"status": "success", "data": {"resultType": "matrix", "result": []}})

    await query_range("up", window_minutes=10)

    params = prom.calls[0]["params"]
    # When start/end are omitted, the server still sends them with a 10-minute window.
    assert "start" in params and "end" in params


@pytest.mark.asyncio
async def test_query_metrics_skips_non_numeric_samples(prom) -> None:  # type: ignore[no-untyped-def]
    """If Prometheus returns a NaN/string-stringly sample, we drop it rather than crash."""
    prom.set_response(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {}, "value": [1718710000.0, "NaN-ish-value"]},
                ],
            },
        }
    )
    result = await query_metrics("up")
    assert result.series[0].samples == []
