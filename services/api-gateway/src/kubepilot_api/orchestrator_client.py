"""Runs LangGraph investigations as background tasks, persists state, emits events.

This is the seam between the FastAPI HTTP layer and the orchestrator's
LangGraph. The HTTP layer creates an InvestigationRecord, then calls
``start_investigation`` which schedules ``_run`` on the event loop. ``_run``
drives the compiled graph via ``astream`` so we can emit one SSE event per
node completion.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

from kubepilot_api.events import InvestigationEvent
from kubepilot_api.pubsub import InvestigationBus
from kubepilot_api.repository import (
    COMPLETED,
    FAILED,
    RUNNING,
    InvestigationRepository,
)
from kubepilot_orch.state import InvestigationState

log = structlog.get_logger(__name__)


class InvestigationOrchestrator:
    """Glues the API gateway, the compiled graph, persistence, and pub/sub."""

    def __init__(
        self,
        compiled_graph: Any,  # langgraph CompiledStateGraph — kept untyped to avoid leaking the dep
        repo: InvestigationRepository,
        bus: InvestigationBus,
    ) -> None:
        self._graph = compiled_graph
        self._repo = repo
        self._bus = bus
        # Track running tasks so the FastAPI shutdown hook can cancel/wait on them.
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    def start_investigation(self, state: InvestigationState) -> None:
        """Spawn a background task that runs the graph for the given state.

        Returns immediately. Caller should already have persisted the initial record.
        """
        task = asyncio.create_task(self._run(state))
        self._tasks[state.incident_id] = task
        task.add_done_callback(lambda t, k=state.incident_id: self._tasks.pop(k, None))

    async def wait_for(self, incident_id: UUID, timeout: float | None = None) -> None:
        """Wait for an in-flight investigation (used by tests)."""
        task = self._tasks.get(incident_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        if not tasks:
            return
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, state: InvestigationState) -> None:
        incident = state.incident_id
        await self._repo.update_status(incident, RUNNING)
        await self._publish(incident, "investigation_started", node=None, payload={
            "query": state.query, "namespace": state.namespace, "service": state.service,
        })

        try:
            current_state: dict[str, Any] = state.model_dump(mode="python")
            async for chunk in self._graph.astream(current_state):
                # chunk is {node_name: state_update}
                for node_name, _update in chunk.items():
                    await self._publish(incident, "node_completed", node=node_name)
                    # Persist a fresh snapshot after each node.
                    # LangGraph doesn't give us the merged state on astream by default,
                    # so we get the final state via aget_state or just re-invoke at the end.
                    # For P1 we rely on the final ainvoke result below for the canonical snapshot.

            # After the graph finishes, fetch the canonical merged state by invoking once more
            # — astream doesn't yield the post-merge state in 0.2.x, so we use ainvoke for the
            # final snapshot. The graph is deterministic given fixed inputs; in P1 with mocked
            # LLMs in tests this is fine, in prod it'll re-call the LLM (small cost).
            #
            # In W9 we'll switch to ``graph.aget_state(thread_id=incident)`` once we wire up
            # a checkpointer thread per investigation. For now astream + best-effort works.
            #
            # NOTE: when running in prod with real LLMs, prefer ainvoke directly and skip the
            # streaming altogether; W9 adds proper graph-level checkpointing for both.
            final_state_raw = await self._graph.ainvoke(state.model_dump(mode="python"))
            final_state = InvestigationState.model_validate(final_state_raw)

            await self._repo.update_state(incident, final_state)
            await self._repo.update_status(incident, COMPLETED)
            await self._publish(
                incident,
                "investigation_completed",
                node=None,
                payload={
                    "confidence": final_state.confidence,
                    "root_cause_category": (
                        final_state.rca.root_cause_category if final_state.rca else None
                    ),
                    "recommendation_count": len(final_state.recommendations),
                },
            )
        except Exception as e:  # noqa: BLE001 — top-level task; we MUST not let it die silently
            log.exception("investigation_failed", incident_id=str(incident))
            await self._repo.update_status(incident, FAILED, error=str(e))
            await self._publish(incident, "investigation_failed", node=None, payload={"error": str(e)})
        finally:
            await self._bus.close(incident)

    async def _publish(
        self,
        incident_id: UUID,
        event_type: str,
        *,
        node: str | None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            InvestigationEvent(
                type=event_type,  # type: ignore[arg-type]
                incident_id=str(incident_id),
                timestamp=datetime.now(timezone.utc),
                node=node,
                payload=payload or {},
            )
        )
