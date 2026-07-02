"""Runs LangGraph investigations as background tasks, persists state, emits events.

This is the seam between the FastAPI HTTP layer and the orchestrator's
LangGraph. The HTTP layer creates an InvestigationRecord, then calls
``start_investigation`` which schedules ``_run`` on the event loop. ``_run``
drives the compiled graph via ``astream`` so we can emit one SSE event per
node completion.

Phase 4 (remediation) adds an interrupt: when a plan needs approval the graph
pauses *before* ``execute_remediation``. ``_run`` detects the pause (via the
checkpointer's ``aget_state``), parks the investigation at ``pending_approval``
and leaves the event bus open. Once the human decision is recorded, the approval
route calls ``start_resume`` → ``_resume`` injects the approvals into the
checkpoint and drives the graph the rest of the way to ``finalize``.
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
    PENDING_APPROVAL,
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

    def set_graph(self, compiled_graph: Any) -> None:
        """Hot-swap the compiled graph (used when UI settings change rebuild it).

        In-flight investigations keep the graph object they captured; new ones use
        the swapped-in graph.
        """
        self._graph = compiled_graph

    def start_investigation(self, state: InvestigationState) -> None:
        """Spawn a background task that runs the graph for the given state.

        Returns immediately. Caller should already have persisted the initial record.
        """
        self._spawn(state.incident_id, self._run(state))

    def start_resume(self, incident_id: UUID) -> None:
        """Resume a remediation-paused investigation after its approval decision.

        Called by the approval route once ``plan_status`` reaches a terminal
        decision (approved / rejected / expired). No-op when the compiled graph
        cannot be resumed (scripted test graphs without a checkpointer) so unit
        tests that exercise the approval endpoints in isolation stay unaffected.
        """
        if not self._resumable():
            log.info("resume_skipped_no_checkpointer", incident_id=str(incident_id))
            return
        self._spawn(incident_id, self._resume(incident_id))

    def _spawn(self, incident_id: UUID, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks[incident_id] = task
        task.add_done_callback(lambda t, k=incident_id: self._tasks.pop(k, None))

    def _resumable(self) -> bool:
        """Whether the compiled graph supports checkpoint-based pause/resume."""
        return hasattr(self._graph, "aget_state") and hasattr(self._graph, "aupdate_state")

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

    # ------------------------------------------------------------------
    # Initial run
    # ------------------------------------------------------------------

    async def _run(self, state: InvestigationState) -> None:
        incident = state.incident_id
        config = {"configurable": {"thread_id": str(incident)}}
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
            final_values = await self._drive(incident, state.model_dump(mode="python"), config)

            # Phase 4: the graph interrupts before ``execute_remediation`` when a
            # plan is present. If it paused with actions to approve, park the
            # investigation and keep the bus open for the eventual resume. An
            # interrupt with an empty plan has nothing to approve — drive straight
            # through so it finalizes normally.
            if await self._is_paused(config):
                if await self._park_if_approvable(incident, final_values):
                    return
                final_values = await self._drive(incident, None, config)

            await self._finalize_success(incident, final_values)
        except Exception as e:
            await self._fail(incident, e)
            return
        await self._bus.close(incident)

    # ------------------------------------------------------------------
    # Resume after approval
    # ------------------------------------------------------------------

    async def _resume(self, incident_id: UUID) -> None:
        config = {"configurable": {"thread_id": str(incident_id)}}
        record = await self._repo.get(incident_id)
        if record is None:
            log.warning("resume_unknown_incident", incident_id=str(incident_id))
            return
        state = InvestigationState.model_validate(record.state_json)
        try:
            # Inject the recorded human decisions into the paused checkpoint so the
            # execute node sees them (the API accumulates approvals on the repo
            # record; the checkpoint's copy is still empty from the interrupt).
            await self._graph.aupdate_state(config, {"approvals": state.approvals})
            await self._repo.update_status(incident_id, RUNNING)
            await self._publish(
                incident_id,
                "investigation_resumed",
                node=None,
                payload={"outcome": state.remediation_outcome},
            )
            final_values = await self._drive(incident_id, None, config)
            await self._finalize_success(incident_id, final_values)
        except Exception as e:
            await self._fail(incident_id, e)
            return
        await self._bus.close(incident_id)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _drive(
        self, incident: UUID, graph_input: dict[str, Any] | None, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Stream the graph to its next stop, emitting SSE progress.

        ``graph_input`` is the initial state dict on first run, or ``None`` to
        resume from a checkpoint. ``stream_mode=["updates", "values"]`` yields
        ``("updates", {node: delta})`` per node (SSE progress) and
        ``("values", full_state)`` (the post-merge snapshot); the last ``values``
        chunk is the state at the stopping point. Returns that snapshot.
        """
        final_values: dict[str, Any] | None = None
        started = datetime.now(UTC)
        ttfb_logged = False
        async for mode, chunk in self._graph.astream(
            graph_input, stream_mode=["updates", "values"], config=config
        ):
            if mode == "updates":
                for node_name in chunk:
                    if not ttfb_logged:
                        # AgentOps: time-to-first-byte (trigger → first node output).
                        ttfb_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
                        log.info("investigation_ttfb", incident_id=str(incident), ttfb_ms=ttfb_ms)
                        ttfb_logged = True
                    await self._publish(incident, "node_completed", node=node_name)
            else:  # "values" — full merged state after the latest node
                final_values = chunk
        if final_values is None:
            raise RuntimeError("graph produced no state; cannot finalize investigation")
        return final_values

    async def _is_paused(self, config: dict[str, Any]) -> bool:
        """Whether the graph interrupted (has pending next nodes) at this thread."""
        if not self._resumable():
            return False
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception:  # a graph without checkpointing can't be paused
            return False
        return bool(getattr(snapshot, "next", None))

    async def _park_if_approvable(self, incident: UUID, final_values: dict[str, Any]) -> bool:
        """Park at ``pending_approval`` when the paused plan has actions to approve.

        Returns True if parked (caller must stop and leave the bus open), False
        when there is nothing to approve (caller should resume to finalize).
        """
        state = InvestigationState.model_validate(final_values)
        plan = state.remediation_plan
        if plan is None or not plan.actions:
            return False
        await self._repo.update_state(incident, state)
        await self._repo.update_status(incident, PENDING_APPROVAL)
        await self._publish(
            incident,
            "investigation_awaiting_approval",
            node=None,
            payload={
                "action_count": len(plan.actions),
                "actions": [
                    {
                        "index": i,
                        "tool": a.tool,
                        "target": a.target,
                        "namespace": a.namespace,
                        "approval_tier": a.approval_tier,
                    }
                    for i, a in enumerate(plan.actions)
                ],
            },
        )
        log.info("investigation_awaiting_approval", incident_id=str(incident))
        return True

    async def _finalize_success(self, incident: UUID, final_values: dict[str, Any]) -> None:
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
                "remediation_outcome": final_state.remediation_outcome,
            },
        )

    async def _fail(self, incident: UUID, error: Exception) -> None:
        log.exception("investigation_failed", incident_id=str(incident))
        await self._repo.update_status(incident, FAILED, error=str(error))
        await self._publish(
            incident, "investigation_failed", node=None, payload={"error": str(error)}
        )
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
