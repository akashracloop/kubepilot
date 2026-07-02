"""Vector + metadata store for incident memory.

- ``InMemoryMemoryStore`` — list-backed cosine search. Dev / tests.
- ``PgVectorMemoryStore`` — pgvector-backed (prod). Lazily imports psycopg so dev
  machines without libpq don't need it; the pgvector extension ships in the
  bundled ``pgvector/pgvector:pg16`` image.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass
class StoredIncident:
    """A concluded incident persisted for later retrieval."""

    incident_id: UUID
    summary: str
    embedding: list[float]
    root_cause_category: str | None = None
    namespace: str | None = None
    service: str | None = None
    outcome: str | None = None
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector dim mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@runtime_checkable
class MemoryStore(Protocol):
    async def add(self, incident: StoredIncident) -> None: ...

    async def search(
        self,
        embedding: list[float],
        *,
        namespace: str | None = None,
        service: str | None = None,
        k: int = 5,
    ) -> list[tuple[StoredIncident, float]]:
        """Return up to ``k`` (incident, cosine_similarity) pairs, most similar first."""
        ...


class InMemoryMemoryStore:
    """In-process store — cosine search over a list. Dev / tests only."""

    def __init__(self) -> None:
        self._items: list[StoredIncident] = []

    async def add(self, incident: StoredIncident) -> None:
        self._items.append(incident)

    async def search(
        self,
        embedding: list[float],
        *,
        namespace: str | None = None,
        service: str | None = None,
        k: int = 5,
    ) -> list[tuple[StoredIncident, float]]:
        scored: list[tuple[StoredIncident, float]] = []
        for item in self._items:
            if namespace is not None and item.namespace not in (None, namespace):
                continue
            scored.append((item, cosine_similarity(embedding, item.embedding)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def __len__(self) -> int:
        return len(self._items)


class PgVectorMemoryStore:
    """pgvector-backed store (prod). Schema is created on first use (idempotent).

    Uses the cosine-distance operator ``<=>``; similarity = 1 - distance.
    """

    def __init__(self, db_url: str, dim: int, table: str = "incident_embeddings") -> None:
        self._db_url = db_url
        self._dim = dim
        self._table = table
        self._pool: Any = None

    async def _ensure(self) -> Any:
        if self._pool is None:
            from psycopg_pool import AsyncConnectionPool

            self._pool = AsyncConnectionPool(self._db_url, open=False)
            await self._pool.open()
            async with self._pool.connection() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        incident_id UUID PRIMARY KEY,
                        summary TEXT NOT NULL,
                        embedding vector({self._dim}) NOT NULL,
                        root_cause_category TEXT,
                        namespace TEXT,
                        service TEXT,
                        outcome TEXT,
                        occurred_at TIMESTAMPTZ
                    )
                    """
                )
        return self._pool

    async def add(self, incident: StoredIncident) -> None:
        pool = await self._ensure()
        async with pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table}
                    (incident_id, summary, embedding, root_cause_category,
                     namespace, service, outcome, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (incident_id) DO UPDATE SET embedding = EXCLUDED.embedding
                """,
                (
                    str(incident.incident_id),
                    incident.summary,
                    incident.embedding,
                    incident.root_cause_category,
                    incident.namespace,
                    incident.service,
                    incident.outcome,
                    incident.occurred_at,
                ),
            )

    async def search(
        self,
        embedding: list[float],
        *,
        namespace: str | None = None,
        service: str | None = None,
        k: int = 5,
    ) -> list[tuple[StoredIncident, float]]:
        pool = await self._ensure()
        where = ""
        params: list[Any] = [embedding]
        if namespace is not None:
            where = "WHERE namespace = %s"
            params.append(namespace)
        params.append(k)
        async with pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT incident_id, summary, root_cause_category, namespace, service,
                       outcome, occurred_at, 1 - (embedding <=> %s) AS similarity
                FROM {self._table}
                {where}
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                [embedding, *params[1:-1], embedding, params[-1]],
            )
            rows = await cur.fetchall()
        out: list[tuple[StoredIncident, float]] = []
        for r in rows:
            out.append(
                (
                    StoredIncident(
                        incident_id=UUID(str(r[0])),
                        summary=r[1],
                        embedding=[],
                        root_cause_category=r[2],
                        namespace=r[3],
                        service=r[4],
                        outcome=r[5],
                        occurred_at=r[6],
                    ),
                    float(r[7]),
                )
            )
        return out

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
