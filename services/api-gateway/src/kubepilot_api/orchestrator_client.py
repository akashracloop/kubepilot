"""Runs LangGraph investigations as background tasks, persists state, emits events.

This is the seam between the FastAPI HTTP layer and the orchestrator's
LangGraph. The HTTP layer creates an InvestigationRecord, then calls
``start_investigation`` which schedules ``_run`` on the event loop. ``_run``
drives the compiled graph via ``astream`` so we can emit one SSE event per
node completion.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from kubepilot_orch.state import InvestigationState

from kubepilot_api.events import InvestigationEvent
from kubepilot_api.pubsub import InvestigationBus
from kubepilot_api.repository import (
    COMPLETED,
    FAILED,
    RUNNING,
    InvestigationRepository,
)

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

    async def wait_for(self, incident_id: UUID, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Wait for an in-flight investigation (used by tests).

        ``timeout`` is forwarded verbatim to ``asyncio.wait_for``; ASYNC109 is a
        false positive here since we are not implementing our own timeout logic.
        """
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
        await self._publish(
            incident,
            "investigation_started",
            node=None,
            payload={
                "query": state.query,
                "namespace": state.namespace,
                "service": state.service,
            },
        )

        try:
            current_state: dict[str, Any] = state.model_dump(mode="python")
            # Single pass over the graph. ``stream_mode=["updates", "values"]`` yields
            # ("updates", {node: delta}) after each node — used for SSE progress — AND
            # ("values", full_state) — the post-merge canonical snapshot. Keeping the
            # last "values" chunk gives us the final state without a second, LLM-costly
            # ``ainvoke`` pass. The ``thread_id`` config is inert without a checkpointer
            # and enables resume once one is attached.
            config = {"configurable": {"thread_id": str(incident)}}
            final_values: dict[str, Any] | None = None
            started = datetime.now(UTC)
            ttfb_logged = False
            async for mode, chunk in self._graph.astream(
                current_state, stream_mode=["updates", "values"], config=config
            ):
                if mode == "updates":
                    for node_name in chunk:
                        if not ttfb_logged:
                            # AgentOps: time-to-first-byte (trigger → first node output).
                            ttfb_ms = int(
                                (datetime.now(UTC) - started).total_seconds() * 1000
                            )
                            log.info(
                                "investigation_ttfb", incident_id=str(incident), ttfb_ms=ttfb_ms
                            )
                            ttfb_logged = True
                        await self._publish(incident, "node_completed", node=node_name)
                else:  # "values" — full merged state after the latest node
                    final_values = chunk

            if final_values is None:
                raise RuntimeError("graph produced no state; cannot finalize investigation")
            final_state = InvestigationState.model_validate(final_values)

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
        except Exception as e:
            log.exception("investigation_failed", incident_id=str(incident))
            await self._repo.update_status(incident, FAILED, error=str(e))
            await self._publish(
                incident, "investigation_failed", node=None, payload={"error": str(e)}
            )
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
                timestamp=datetime.now(UTC),
                node=node,
                payload=payload or {},
            )
        )
