"""query_alerts — currently firing/pending alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp_prom import client
from mcp_prom.models import Alert, AlertsView
from mcp_prom.tools.base import Tool, register


async def query_alerts(state: str | None = None) -> AlertsView:
    """Return alerts known to Prometheus (uses /api/v1/alerts).

    `state` filters to firing|pending|inactive. If omitted, all alerts are returned.
    """
    data = await client.get("/api/v1/alerts")
    raw = data["data"].get("alerts", []) or []

    alerts = [_to_alert(a) for a in raw]
    if state:
        alerts = [a for a in alerts if a.state == state]
    return AlertsView(alerts=alerts)


def _to_alert(raw: dict[str, Any]) -> Alert:
    labels = raw.get("labels", {}) or {}
    annotations = raw.get("annotations", {}) or {}
    active_at = raw.get("activeAt")
    return Alert(
        name=labels.get("alertname", ""),
        state=raw.get("state", "inactive"),
        severity=labels.get("severity"),
        summary=annotations.get("summary"),
        description=annotations.get("description"),
        labels=labels,
        annotations=annotations,
        active_since=_parse_iso(active_at),
    )


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {
            "type": ["string", "null"],
            "enum": ["firing", "pending", "inactive", None],
            "description": "Optional state filter",
        },
    },
    "additionalProperties": False,
}


register(
    Tool(
        name="query_alerts",
        description=(
            "Return Prometheus alerts, optionally filtered by state (firing/pending/inactive). "
            "Useful for cross-checking an incident against existing alerting rules."
        ),
        parameters=_SCHEMA,
        handler=query_alerts,
    )
)
