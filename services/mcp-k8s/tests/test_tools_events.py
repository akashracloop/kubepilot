"""Unit tests for get_events."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mcp_k8s.tools.events import get_events


def _event(reason: str, when: datetime, *, kind: str = "Pod", name: str = "p1") -> SimpleNamespace:
    return SimpleNamespace(
        type="Warning",
        reason=reason,
        message=f"{reason} happened",
        count=1,
        first_timestamp=when,
        last_timestamp=when,
        event_time=None,
        involved_object=SimpleNamespace(kind=kind, name=name, namespace="prod"),
        source=SimpleNamespace(component="kubelet"),
    )


@pytest.mark.asyncio
async def test_get_events_sorted_newest_first(core_v1: MagicMock) -> None:
    older = _event("BackOff", datetime(2026, 6, 23, 10, 0, tzinfo=UTC))
    newer = _event("OOMKilling", datetime(2026, 6, 23, 10, 5, tzinfo=UTC))
    core_v1.list_namespaced_event.return_value = SimpleNamespace(items=[older, newer])

    events = await get_events("prod")
    assert events[0].reason == "OOMKilling"
    assert events[1].reason == "BackOff"


@pytest.mark.asyncio
async def test_get_events_applies_object_filter(core_v1: MagicMock) -> None:
    core_v1.list_namespaced_event.return_value = SimpleNamespace(items=[])
    await get_events("prod", related_to_kind="Pod", related_to_name="payment-0")
    call = core_v1.list_namespaced_event.call_args
    assert call.kwargs["namespace"] == "prod"
    assert "involvedObject.kind=Pod" in call.kwargs["field_selector"]
    assert "involvedObject.name=payment-0" in call.kwargs["field_selector"]


@pytest.mark.asyncio
async def test_get_events_respects_limit(core_v1: MagicMock) -> None:
    items = [_event(f"R{i}", datetime(2026, 6, 23, 10, i, tzinfo=UTC)) for i in range(20)]
    core_v1.list_namespaced_event.return_value = SimpleNamespace(items=items)
    events = await get_events("prod", limit=5)
    assert len(events) == 5
