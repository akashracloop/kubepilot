"""Cluster knowledge graph — services ↔ owners ↔ dependencies ↔ SLOs (Phase 3).

A relational record per service: who owns it, what it depends on, what depends on
it, its SLOs, and its last deploy. The primary operation is an *exact* lookup by
(namespace, service) — "who owns checkout-service and what does it call" — plus a
reverse lookup ("what calls payments-db") used to correlate a dependency's failure
to the services that ride on it.

Two stores, mirroring the Phase 2 memory seam:
- ``InMemoryKnowledgeStore`` — dict-backed. Dev / tests.
- ``PgKnowledgeStore``       — Postgres-backed (prod), reusing the bundled
  ``pgvector/pgvector:pg16`` Postgres. Schema created on first use (idempotent).

Semantic (pgvector) fuzzy service-name resolution is a later refinement; the
exact relational lookup is what the RCA/K8s agents need first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kubepilot_orch.state import ServiceKnowledge


@dataclass
class ServiceRecord:
    """One service's entry in the knowledge graph (store-level; adds namespace)."""

    service: str
    namespace: str
    owner: str | None = None
    dependencies: list[str] = field(default_factory=list)  # services this one calls
    dependents: list[str] = field(default_factory=list)  # services that call this one
    slos: dict[str, Any] = field(default_factory=dict)
    last_deploy: str | None = None
    notes: str | None = None

    def to_knowledge(self) -> ServiceKnowledge:
        """Project to the state-level ``ServiceKnowledge`` (drops namespace)."""
        return ServiceKnowledge(
            service=self.service,
            owner=self.owner,
            dependencies=list(self.dependencies),
            dependents=list(self.dependents),
            slos=dict(self.slos),
            last_deploy=self.last_deploy,
            notes=self.notes,
        )


@runtime_checkable
class KnowledgeStore(Protocol):
    async def upsert(self, record: ServiceRecord) -> None: ...

    async def get(self, service: str, *, namespace: str | None = None) -> ServiceRecord | None:
        """Exact lookup by service (optionally scoped to a namespace)."""
        ...

    async def find_dependents(
        self, service: str, *, namespace: str | None = None
    ) -> list[ServiceRecord]:
        """Records that declare ``service`` as a dependency (reverse edge)."""
        ...


class InMemoryKnowledgeStore:
    """In-process store — dict keyed by (namespace, service). Dev / tests only."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ServiceRecord] = {}

    async def upsert(self, record: ServiceRecord) -> None:
        self._items[(record.namespace, record.service)] = record

    async def get(self, service: str, *, namespace: str | None = None) -> ServiceRecord | None:
        if namespace is not None:
            return self._items.get((namespace, service))
        # No namespace given: first match across namespaces (stable insertion order).
        for (_ns, svc), rec in self._items.items():
            if svc == service:
                return rec
        return None

    async def find_dependents(
        self, service: str, *, namespace: str | None = None
    ) -> list[ServiceRecord]:
        out: list[ServiceRecord] = []
        for (ns, _svc), rec in self._items.items():
            if namespace is not None and ns != namespace:
                continue
            if service in rec.dependencies:
                out.append(rec)
        return out

    def __len__(self) -> int:
        return len(self._items)


class PgKnowledgeStore:
    """Postgres-backed knowledge store (prod). Schema created on first use.

    Lazily imports psycopg so dev machines without libpq don't need it.
    """

    def __init__(self, db_url: str, table: str = "service_knowledge") -> None:
        self._db_url = db_url
        self._table = table
        self._pool: Any = None

    async def _ensure(self) -> Any:
        if self._pool is None:
            from psycopg_pool import AsyncConnectionPool

            self._pool = AsyncConnectionPool(self._db_url, open=False)
            await self._pool.open()
            async with self._pool.connection() as conn:
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        namespace TEXT NOT NULL,
                        service TEXT NOT NULL,
                        owner TEXT,
                        dependencies JSONB NOT NULL DEFAULT '[]',
                        dependents JSONB NOT NULL DEFAULT '[]',
                        slos JSONB NOT NULL DEFAULT '{{}}',
                        last_deploy TEXT,
                        notes TEXT,
                        PRIMARY KEY (namespace, service)
                    )
                    """
                )
        return self._pool

    async def upsert(self, record: ServiceRecord) -> None:
        import json

        pool = await self._ensure()
        async with pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table}
                    (namespace, service, owner, dependencies, dependents, slos, last_deploy, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (namespace, service) DO UPDATE SET
                    owner = EXCLUDED.owner,
                    dependencies = EXCLUDED.dependencies,
                    dependents = EXCLUDED.dependents,
                    slos = EXCLUDED.slos,
                    last_deploy = EXCLUDED.last_deploy,
                    notes = EXCLUDED.notes
                """,
                (
                    record.namespace,
                    record.service,
                    record.owner,
                    json.dumps(record.dependencies),
                    json.dumps(record.dependents),
                    json.dumps(record.slos),
                    record.last_deploy,
                    record.notes,
                ),
            )

    async def get(self, service: str, *, namespace: str | None = None) -> ServiceRecord | None:
        pool = await self._ensure()
        where = "service = %s"
        params: list[Any] = [service]
        if namespace is not None:
            where += " AND namespace = %s"
            params.append(namespace)
        async with pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT namespace, service, owner, dependencies, dependents, slos, "
                f"last_deploy, notes FROM {self._table} WHERE {where} LIMIT 1",
                params,
            )
            row = await cur.fetchone()
        return _row_to_record(row) if row else None

    async def find_dependents(
        self, service: str, *, namespace: str | None = None
    ) -> list[ServiceRecord]:
        import json

        pool = await self._ensure()
        where = "dependencies @> %s::jsonb"
        params: list[Any] = [json.dumps([service])]
        if namespace is not None:
            where += " AND namespace = %s"
            params.append(namespace)
        async with pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT namespace, service, owner, dependencies, dependents, slos, "
                f"last_deploy, notes FROM {self._table} WHERE {where}",
                params,
            )
            rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def _row_to_record(row: Any) -> ServiceRecord:
    """Map a DB row to a ServiceRecord (psycopg returns JSONB columns as py objects)."""
    return ServiceRecord(
        namespace=row[0],
        service=row[1],
        owner=row[2],
        dependencies=list(row[3] or []),
        dependents=list(row[4] or []),
        slos=dict(row[5] or {}),
        last_deploy=row[6],
        notes=row[7],
    )
