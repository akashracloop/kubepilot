"""get_events — namespaced k8s events, optionally filtered by object."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp_k8s.client import get_core_v1
from mcp_k8s.models import K8sEvent
from mcp_k8s.tools.base import Tool, register, to_thread


async def get_events(
    namespace: str,
    related_to_kind: str | None = None,
    related_to_name: str | None = None,
    limit: int = 50,
) -> list[K8sEvent]:
    api = get_core_v1()
    selectors: list[str] = []
    if related_to_kind:
        selectors.append(f"involvedObject.kind={related_to_kind}")
    if related_to_name:
        selectors.append(f"involvedObject.name={related_to_name}")
    field_selector = ",".join(selectors) if selectors else None

    raw = await to_thread(
        api.list_namespaced_event,
        namespace=namespace,
        field_selector=field_selector,
    )
    events = [_event_to_model(e) for e in raw.items]
    events.sort(key=lambda e: e.last_seen or datetime.min, reverse=True)
    return events[:limit]


def _event_to_model(e: Any) -> K8sEvent:
    return K8sEvent(
        type=e.type or "Normal",
        reason=e.reason or "",
        message=(e.message or "").strip(),
        count=int(e.count or 1),
        first_seen=e.first_timestamp or e.event_time,
        last_seen=e.last_timestamp or e.event_time,
        involved_object_kind=getattr(e.involved_object, "kind", None),
        involved_object_name=getattr(e.involved_object, "name", None),
        involved_object_namespace=getattr(e.involved_object, "namespace", None),
        source_component=getattr(e.source, "component", None) if e.source else None,
    )


_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string"},
        "related_to_kind": {
            "type": ["string", "null"],
            "description": "Filter to events about a specific object kind (Pod, Deployment, etc.)",
        },
        "related_to_name": {
            "type": ["string", "null"],
            "description": "Filter to events about a specific object name",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    },
    "required": ["namespace"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_events",
        description="List recent Kubernetes events in a namespace, optionally filtered by object.",
        parameters=_SCHEMA,
        handler=get_events,
    )
)
