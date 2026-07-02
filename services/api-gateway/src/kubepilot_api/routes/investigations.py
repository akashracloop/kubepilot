"""Investigation REST + SSE routes.

Endpoints:
  POST   /investigations              start a new investigation
  GET    /investigations              list (pagination)
  GET    /investigations/{id}         full record snapshot
  GET    /investigations/{id}/stream  SSE stream of progress events
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from kubepilot_api.orchestrator_client import InvestigationOrchestrator
from kubepilot_api.pubsub import InvestigationBus
from kubepilot_api.repository import InvestigationRecord, InvestigationRepository
from kubepilot_orch.state import InvestigationState


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class CreateInvestigationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    namespace: str = Field(min_length=1, max_length=253)
    service: str | None = Field(default=None, max_length=253)
    time_window_minutes: int = Field(default=30, ge=1, le=1440)


class CreateInvestigationResponse(BaseModel):
    incident_id: UUID
    status: str
    created_at: datetime


class InvestigationDetail(BaseModel):
    incident_id: UUID
    status: str
    query: str
    namespace: str
    service: str | None
    created_at: datetime
    updated_at: datetime
    error: str | None
    state: dict[str, Any]


class InvestigationList(BaseModel):
    items: list[InvestigationDetail]
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Dependency accessors — bound at app-build time via app.state
# ---------------------------------------------------------------------------


def _repo(request: Request) -> InvestigationRepository:
    return request.app.state.repo  # type: ignore[no-any-return]


def _orchestrator(request: Request) -> InvestigationOrchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


def _bus(request: Request) -> InvestigationBus:
    return request.app.state.bus  # type: ignore[no-any-return]


def make_router(*, auth_dep) -> APIRouter:  # type: ignore[no-untyped-def]
    """Build the routes with the configured auth dependency wired in."""

    router = APIRouter(prefix="/investigations", dependencies=[Depends(auth_dep)])

    @router.post("", response_model=CreateInvestigationResponse, status_code=status.HTTP_202_ACCEPTED)
    async def create(
        body: CreateInvestigationRequest,
        request: Request,
    ) -> CreateInvestigationResponse:
        incident_id = uuid4()
        now = datetime.now(timezone.utc)

        initial_state = InvestigationState(
            incident_id=incident_id,
            query=body.query,
            namespace=body.namespace,
            service=body.service,
            time_window_minutes=body.time_window_minutes,
            started_at=now,
        )
        record = InvestigationRecord.from_initial(
            incident_id=incident_id,
            query=body.query,
            namespace=body.namespace,
            service=body.service,
            state=initial_state,
        )
        await _repo(request).create(record)
        _orchestrator(request).start_investigation(initial_state)

        return CreateInvestigationResponse(
            incident_id=incident_id, status=record.status, created_at=now
        )

    @router.get("", response_model=InvestigationList)
    async def list_(
        request: Request,
        limit: int = 50,
        offset: int = 0,
    ) -> InvestigationList:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        records = await _repo(request).list(limit=limit, offset=offset)
        return InvestigationList(
            items=[_to_detail(r) for r in records],
            limit=limit,
            offset=offset,
        )

    @router.get("/{incident_id}", response_model=InvestigationDetail)
    async def get(incident_id: UUID, request: Request) -> InvestigationDetail:
        record = await _repo(request).get(incident_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Investigation {incident_id} not found")
        return _to_detail(record)

    @router.get("/{incident_id}/stream")
    async def stream(incident_id: UUID, request: Request) -> EventSourceResponse:
        # First, confirm the investigation exists so we can return 404 cleanly.
        record = await _repo(request).get(incident_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Investigation {incident_id} not found")

        bus = _bus(request)

        async def _events():  # type: ignore[no-untyped-def]
            async with bus.subscribe(incident_id) as queue:
                # If the investigation has already finished, emit one synthetic
                # snapshot event and close — clients connecting late get state.
                fresh = await _repo(request).get(incident_id)
                if fresh and fresh.status in {"completed", "failed"}:
                    yield {
                        "event": "investigation_completed"
                        if fresh.status == "completed"
                        else "investigation_failed",
                        "data": _detail_to_json(_to_detail(fresh)),
                    }
                    return

                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Heartbeat to keep proxies happy.
                        yield {"event": "ping", "data": "{}"}
                        continue
                    if event is None:
                        return  # sentinel — investigation closed
                    yield event.sse()

        return EventSourceResponse(_events())

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_detail(record: InvestigationRecord) -> InvestigationDetail:
    return InvestigationDetail(
        incident_id=record.incident_id,
        status=record.status,
        query=record.query,
        namespace=record.namespace,
        service=record.service,
        created_at=record.created_at,
        updated_at=record.updated_at,
        error=record.error,
        state=record.state_json,
    )


def _detail_to_json(detail: InvestigationDetail) -> str:
    return detail.model_dump_json()
