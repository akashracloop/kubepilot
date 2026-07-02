"""Server-sent event types streamed to the API gateway client.

The /investigations/{id}/stream endpoint emits these as ``event: <type>``
with a JSON body. Clients (UI, CLI) subscribe to follow an investigation's
progress in real time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "investigation_started",
    "node_started",  # any graph node — kubernetes / metrics / logs / rca / recommendation / finalize
    "node_completed",
    # Phase 4 remediation lifecycle: the graph paused for HITL approval, then
    # resumed once the decision was recorded.
    "investigation_awaiting_approval",
    "investigation_resumed",
    "investigation_completed",
    "investigation_failed",
]


class InvestigationEvent(BaseModel):
    type: EventType
    incident_id: str
    timestamp: datetime
    node: str | None = None  # set for node_started / node_completed
    payload: dict[str, Any] = Field(default_factory=dict)

    def sse(self) -> dict[str, str]:
        """Render as a starlette EventSourceResponse-compatible message."""
        return {"event": self.type, "data": self.model_dump_json(exclude={"type"})}
