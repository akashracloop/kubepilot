"""list_targets — scrape target discovery (for diagnosing missing metrics)."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from mcp_prom import client
from mcp_prom.models import Target, TargetsView
from mcp_prom.tools.base import Tool, register


async def list_targets(state: str = "any") -> TargetsView:
    """Return active scrape targets.

    `state` filters by Prometheus's view: "active", "dropped", or "any" (default).
    Useful when the agent finds that 'expected metric is missing' — it can check
    whether the target is even being scraped.
    """
    data = await client.get("/api/v1/targets", params={"state": state})
    payload = data["data"]
    active_raw = payload.get("activeTargets", []) or []
    dropped_raw = payload.get("droppedTargets", []) or []

    active = [_to_target(t) for t in active_raw]
    return TargetsView(active=active, dropped_count=len(dropped_raw))


def _to_target(raw: dict[str, Any]) -> Target:
    labels = raw.get("labels", {}) or {}
    return Target(
        job=labels.get("job", ""),
        instance=labels.get("instance", ""),
        health=raw.get("health", "unknown"),
        last_error=raw.get("lastError") or None,
        last_scrape_seconds_ago=_seconds_since(raw.get("lastScrape")),
        labels=labels,
    )


def _seconds_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        from datetime import datetime

        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return (datetime.now(UTC) - ts).total_seconds()
    except (ValueError, TypeError):
        return None


_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "enum": ["any", "active", "dropped"],
            "default": "any",
        },
    },
    "additionalProperties": False,
}


register(
    Tool(
        name="list_targets",
        description=(
            "List Prometheus scrape targets with their health and last error. "
            "Useful when an expected metric is missing — verifies the target is being scraped."
        ),
        parameters=_SCHEMA,
        handler=list_targets,
    )
)
