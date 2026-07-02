"""Investigation persistence — Protocol + Postgres + in-memory implementations.

The orchestrator and HTTP routes depend on ``InvestigationRepository``, not on
asyncpg directly, so tests can swap a real DB for the in-memory implementation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from kubepilot_orch.state import InvestigationState

# Status lifecycle.
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"


@dataclass
class InvestigationRecord:
    """The persisted shape — superset of InvestigationState plus lifecycle metadata."""

    incident_id: UUID
    status: str  # "pending" | "running" | "completed" | "failed"
    query: str
    namespace: str
    service: str | None
    created_at: datetime
    updated_at: datetime
    state_json: dict[str, Any]  # snapshot of InvestigationState
    error: str | None = None

    @classmethod
    def from_initial(
        cls,
        incident_id: UUID,
        query: str,
        namespace: str,
        service: str | None,
        state: InvestigationState,
    ) -> InvestigationRecord:
        now = datetime.now(UTC)
        return cls(
            incident_id=incident_id,
            status=PENDING,
            query=query,
            namespace=namespace,
            service=service,
            created_at=now,
            updated_at=now,
            state_json=state.model_dump(mode="json"),
        )


class InvestigationRepository(Protocol):
    """Persistence interface for investigations."""

    async def create(self, record: InvestigationRecord) -> None: ...
    async def get(self, incident_id: UUID) -> InvestigationRecord | None: ...
    async def list(self, *, limit: int = 50, offset: int = 0) -> list[InvestigationRecord]: ...
    async def update_status(
        self, incident_id: UUID, status: str, *, error: str | None = None
    ) -> None: ...
    async def update_state(self, incident_id: UUID, state: InvestigationState) -> None: ...
    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation — used in tests and dev runs without Postgres
# ---------------------------------------------------------------------------


@dataclass
class InMemoryInvestigationRepository(InvestigationRepository):
    records: dict[UUID, InvestigationRecord] = field(default_factory=dict)

    async def create(self, record: InvestigationRecord) -> None:
        self.records[record.incident_id] = record

    async def get(self, incident_id: UUID) -> InvestigationRecord | None:
        return self.records.get(incident_id)

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[InvestigationRecord]:
        items = sorted(self.records.values(), key=lambda r: r.created_at, reverse=True)
        return items[offset : offset + limit]

    async def update_status(
        self, incident_id: UUID, status: str, *, error: str | None = None
    ) -> None:
        record = self.records.get(incident_id)
        if record is None:
            return
        record.status = status
        record.updated_at = datetime.now(UTC)
        if error is not None:
            record.error = error

    async def update_state(self, incident_id: UUID, state: InvestigationState) -> None:
        record = self.records.get(incident_id)
        if record is None:
            return
        record.state_json = state.model_dump(mode="json")
        record.updated_at = datetime.now(UTC)

    async def aclose(self) -> None:
        # Nothing to close.
        return


# ---------------------------------------------------------------------------
# Postgres implementation — schema bootstrap + asyncpg queries
# ---------------------------------------------------------------------------


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS investigations (
    incident_id  UUID PRIMARY KEY,
    status       TEXT NOT NULL,
    query        TEXT NOT NULL,
    namespace    TEXT NOT NULL,
    service      TEXT,
    error        TEXT,
    state_json   JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS investigations_created_at_idx
    ON investigations (created_at DESC);
"""


class PostgresInvestigationRepository(InvestigationRepository):
    """asyncpg-backed implementation. Pool is created lazily."""

    def __init__(self, url: str, pool_size: int = 10) -> None:
        self._url = url
        self._pool_size = pool_size
        self._pool: Any | None = None

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            import asyncpg  # local import keeps the package optional for in-memory users

            self._pool = await asyncpg.create_pool(self._url, max_size=self._pool_size)
            async with self._pool.acquire() as conn:
                await conn.execute(_SCHEMA_DDL)
        return self._pool

    async def create(self, record: InvestigationRecord) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO investigations
                    (incident_id, status, query, namespace, service, state_json, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
                """,
                record.incident_id,
                record.status,
                record.query,
                record.namespace,
                record.service,
                json.dumps(record.state_json),
                record.created_at,
                record.updated_at,
            )

    async def get(self, incident_id: UUID) -> InvestigationRecord | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM investigations WHERE incident_id = $1", incident_id
            )
        return _row_to_record(row) if row else None

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[InvestigationRecord]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM investigations ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                limit,
                offset,
            )
        return [_row_to_record(r) for r in rows]

    async def update_status(
        self, incident_id: UUID, status: str, *, error: str | None = None
    ) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE investigations
                   SET status = $2, error = $3, updated_at = $4
                 WHERE incident_id = $1
                """,
                incident_id,
                status,
                error,
                datetime.now(UTC),
            )

    async def update_state(self, incident_id: UUID, state: InvestigationState) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE investigations
                   SET state_json = $2::jsonb, updated_at = $3
                 WHERE incident_id = $1
                """,
                incident_id,
                json.dumps(state.model_dump(mode="json")),
                datetime.now(UTC),
            )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def _row_to_record(row: Iterable) -> InvestigationRecord:  # type: ignore[type-arg]
    d = dict(row)  # asyncpg.Record → dict
    state_json = d["state_json"]
    if isinstance(state_json, str):
        state_json = json.loads(state_json)
    return InvestigationRecord(
        incident_id=d["incident_id"],
        status=d["status"],
        query=d["query"],
        namespace=d["namespace"],
        service=d["service"],
        error=d["error"],
        state_json=state_json,
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )
