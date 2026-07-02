"""Per-investigation in-memory pub/sub for SSE streaming.

A single asyncio.Queue per investigation, with one publisher (the orchestrator
task) and N subscribers (each SSE client connection). When the investigation
ends we emit a sentinel and drain.

Phase 1 single-replica design: queues live in process memory. Multi-replica
(Phase 2+) will need Redis pub/sub or NATS — but the public surface here is
intentionally minimal so swapping is a one-file change.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from kubepilot_api.events import InvestigationEvent


class InvestigationBus:
    """Fanout queue manager keyed by incident_id."""

    def __init__(self) -> None:
        self._subscribers: dict[UUID, list[asyncio.Queue[InvestigationEvent | None]]] = defaultdict(
            list
        )
        self._lock = asyncio.Lock()

    async def publish(self, event: InvestigationEvent) -> None:
        incident_id = UUID(event.incident_id)
        async with self._lock:
            queues = list(self._subscribers.get(incident_id, []))
        for q in queues:
            await q.put(event)

    async def close(self, incident_id: UUID) -> None:
        """Emit sentinel and remove subscribers for a completed investigation."""
        async with self._lock:
            queues = self._subscribers.pop(incident_id, [])
        for q in queues:
            await q.put(None)  # sentinel

    @asynccontextmanager
    async def subscribe(self, incident_id: UUID) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue[InvestigationEvent | None] = asyncio.Queue()
        async with self._lock:
            self._subscribers[incident_id].append(q)
        try:
            yield q
        finally:
            async with self._lock:
                if incident_id in self._subscribers:
                    with contextlib.suppress(ValueError):
                        self._subscribers[incident_id].remove(q)
                    if not self._subscribers[incident_id]:
                        del self._subscribers[incident_id]
