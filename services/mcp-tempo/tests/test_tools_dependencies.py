"""Tests for service_dependency_map."""

from __future__ import annotations

import pytest
from mcp_tempo.tools.dependencies import service_dependency_map


@pytest.mark.asyncio
async def test_service_dependency_map_filters_to_neighbours(tempo) -> None:  # type: ignore[no-untyped-def]
    tempo.set_response(
        {
            "edges": [
                {
                    "caller": "checkout",
                    "callee": "payments",
                    "callCount": 120,
                    "errorCount": 3,
                    "p99Ms": 180.5,
                },
                {
                    "caller": "payments",
                    "callee": "ledger",
                    "callCount": 90,
                    "errorCount": 0,
                    "p99Ms": 45.0,
                },
                {
                    "caller": "frontend",
                    "callee": "catalog",
                    "callCount": 10,
                    "errorCount": 0,
                    "p99Ms": 5.0,
                },
            ]
        }
    )

    dep = await service_dependency_map("payments")

    assert dep.service == "payments"
    # frontend→catalog does not touch payments, so it is dropped.
    assert len(dep.edges) == 2
    pairs = {(e.caller, e.callee) for e in dep.edges}
    assert pairs == {("checkout", "payments"), ("payments", "ledger")}

    upstream = next(e for e in dep.edges if e.caller == "checkout")
    assert upstream.call_count == 120
    assert upstream.error_count == 3
    assert upstream.p99_ms == pytest.approx(180.5)
    assert tempo.calls[0]["path"] == "/api/metrics/service_graph"
