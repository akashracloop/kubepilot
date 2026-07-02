"""Tests for list_targets + query_alerts."""

from __future__ import annotations

import pytest
from mcp_prom.tools.alerts import query_alerts
from mcp_prom.tools.targets import list_targets


@pytest.mark.asyncio
async def test_list_targets_summarizes_health(prom) -> None:  # type: ignore[no-untyped-def]
    prom.set_response(
        {
            "status": "success",
            "data": {
                "activeTargets": [
                    {
                        "labels": {"job": "kubelet", "instance": "node-a:10250"},
                        "health": "up",
                        "lastScrape": "2026-06-23T10:00:00Z",
                    },
                    {
                        "labels": {"job": "node-exporter", "instance": "node-b:9100"},
                        "health": "down",
                        "lastError": "connection refused",
                    },
                ],
                "droppedTargets": [{"discoveredLabels": {}}, {"discoveredLabels": {}}],
            },
        }
    )

    view = await list_targets()

    assert len(view.active) == 2
    assert view.active[0].job == "kubelet"
    assert view.active[0].health == "up"
    assert view.active[1].health == "down"
    assert view.active[1].last_error == "connection refused"
    assert view.dropped_count == 2


@pytest.mark.asyncio
async def test_query_alerts_filter_by_state(prom) -> None:  # type: ignore[no-untyped-def]
    prom.set_response(
        {
            "status": "success",
            "data": {
                "alerts": [
                    {
                        "labels": {"alertname": "HighMemory", "severity": "critical"},
                        "annotations": {"summary": "Memory > 90%"},
                        "state": "firing",
                        "activeAt": "2026-06-23T10:00:00Z",
                    },
                    {
                        "labels": {"alertname": "FlakyTarget"},
                        "annotations": {},
                        "state": "pending",
                    },
                ]
            },
        }
    )

    firing = await query_alerts(state="firing")
    assert len(firing.alerts) == 1
    assert firing.alerts[0].name == "HighMemory"
    assert firing.alerts[0].severity == "critical"

    pending = await query_alerts(state="pending")
    assert len(pending.alerts) == 1
    assert pending.alerts[0].name == "FlakyTarget"

    all_alerts = await query_alerts()
    assert len(all_alerts.alerts) == 2
