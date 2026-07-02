"""Vector + metadata store for incident memory.

- ``InMemoryMemoryStore`` — list-backed cosine search. Dev / tests.
- ``PgVectorMemoryStore`` — pgvector-backed (prod). Lazily imports psycopg so dev
  machines without libpq don't need it; the pgvector extension ships in the
  bundled ``pgvector/pgvector:pg16`` image.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

# Weight on dense (embedding) similarity when blending with the lexical score in
# hybrid retrieval; the remainder goes to the lexical term. Only applied when a
# ``query_text`` is supplied — otherwise search is pure dense cosine (unchanged).
HYBRID_DENSE_WEIGHT = 0.7

_WORD_RE = re.compile(r"[a-z0-9]+")


def lexical_overlap(query: str, text: str) -> float:
    """Jaccard token overlap in [0, 1] — the lightweight BM25-style lexical signal."""
    q = set(_WORD_RE.findall(query.lower()))
    t = set(_WORD_RE.findall(text.lower()))
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


def _blend(dense: float, query_text: str | None, summary: str) -> float:
    """Combine dense cosine with the lexical overlap when a query_text is given."""
    if not query_text:
        return dense
    lex = lexical_overlap(query_text, summary)
    return HYBRID_DENSE_WEIGHT * dense + (1.0 - HYBRID_DENSE_WEIGHT) * lex


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


def _vec_literal(embedding: list[float]) -> str:
    """pgvector text literal, e.g. '[0.1,0.2]'. Combined with a ``::vector`` cast
    so the value is a ``vector`` (not a ``double precision[]``) for the `<=>` op."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


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
        query_text: str | None = None,
    ) -> list[tuple[StoredIncident, float]]:
        """Return up to ``k`` (incident, score) pairs, most similar first.

        ``query_text`` enables hybrid ranking (dense cosine blended with a lexical
        overlap on the summary); without it the score is pure dense cosine.
        """
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
        query_text: str | None = None,
    ) -> list[tuple[StoredIncident, float]]:
        scored: list[tuple[StoredIncident, float]] = []
        for item in self._items:
            if namespace is not None and item.namespace not in (None, namespace):
                continue
            dense = cosine_similarity(embedding, item.embedding)
            scored.append((item, _blend(dense, query_text, item.summary)))
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
                VALUES (%s, %s, %s::vector, %s, %s, %s, %s, %s)
                ON CONFLICT (incident_id) DO UPDATE SET embedding = EXCLUDED.embedding
                """,
                (
                    str(incident.incident_id),
                    incident.summary,
                    _vec_literal(incident.embedding),
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
        query_text: str | None = None,
    ) -> list[tuple[StoredIncident, float]]:
        pool = await self._ensure()
        vec = _vec_literal(embedding)
        where = "WHERE namespace = %s" if namespace is not None else ""
        ns_param: list[Any] = [namespace] if namespace is not None else []

        if query_text:
            # Hybrid: blend dense cosine with a full-text lexical rank on the summary
            # (ts_rank_cd normalization flag 32 keeps the term in [0, 1)).
            score_expr = (
                f"({HYBRID_DENSE_WEIGHT} * (1 - (embedding <=> %s::vector)) "
                f"+ {1.0 - HYBRID_DENSE_WEIGHT} * "
                "COALESCE(ts_rank_cd(to_tsvector('english', summary), "
                "plainto_tsquery('english', %s), 32), 0))"
            )
            select_params: list[Any] = [vec, query_text]
        else:
            score_expr = "1 - (embedding <=> %s::vector)"
            select_params = [vec]

        async with pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT incident_id, summary, root_cause_category, namespace, service,
                       outcome, occurred_at, {score_expr} AS similarity
                FROM {self._table}
                {where}
                ORDER BY similarity DESC
                LIMIT %s
                """,
                [*select_params, *ns_param, k],
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
