"""Integration tests for the Postgres-backed stores (Phase 2/3).

Real pgvector + Postgres round-trips — the layer the deterministic suite can't
cover. Skipped unless ``KUBEPILOT_TEST_DB_URL`` points at a Postgres with the
``vector`` extension available (e.g. the bundled ``pgvector/pgvector:pg16``):

    KUBEPILOT_TEST_DB_URL=postgresql://kubepilot:kubepilot@localhost:5432/kubepilot \
        uv run pytest -m integration services/orchestrator/tests/test_integration_stores.py

Each test uses a unique table name and drops it on teardown, so runs are isolated
and leave no residue.
"""

from __future__ import annotations

import os
import uuid

import pytest
from kubepilot_orch.knowledge.graph import PgKnowledgeStore, ServiceRecord
from kubepilot_orch.memory.embedder import HashEmbedder
from kubepilot_orch.memory.store import PgVectorMemoryStore, StoredIncident

pytestmark = pytest.mark.integration

_DB_URL = os.getenv("KUBEPILOT_TEST_DB_URL")
_skip = pytest.mark.skipif(
    not _DB_URL, reason="set KUBEPILOT_TEST_DB_URL to run Postgres integration tests"
)


def _table(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _drop(store, table: str) -> None:  # type: ignore[no-untyped-def]
    pool = await store._ensure()
    async with pool.connection() as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {table}")


@_skip
@pytest.mark.asyncio
async def test_pgvector_memory_roundtrip() -> None:
    table = _table("it_incidents")
    store = PgVectorMemoryStore(_DB_URL, dim=256, table=table)  # type: ignore[arg-type]
    embedder = HashEmbedder(dim=256)
    try:
        (vec,) = await embedder.embed(["checkout-service latency regression after a deploy"])
        incident_id = uuid.uuid4()
        await store.add(
            StoredIncident(
                incident_id=incident_id,
                summary="checkout latency regression",
                embedding=vec,
                root_cause_category="LatencyRegression",
                namespace="prod",
                service="checkout-service",
                outcome="rolled back",
            )
        )
        (near,) = await embedder.embed(["checkout-service is slow after the latest release"])
        results = await store.search(near, namespace="prod", k=3)
        assert results, "expected at least one hit"
        top, similarity = results[0]
        assert top.incident_id == incident_id
        assert top.service == "checkout-service"
        assert 0.0 <= similarity <= 1.0

        # Hybrid path: exercise the ts_rank_cd / plainto_tsquery lexical blend.
        hybrid = await store.search(
            near, namespace="prod", k=3, query_text="checkout latency regression"
        )
        assert hybrid and hybrid[0][0].incident_id == incident_id
        assert 0.0 <= hybrid[0][1] <= 1.0
    finally:
        await _drop(store, table)
        await store.aclose()


@_skip
@pytest.mark.asyncio
async def test_pg_knowledge_roundtrip_and_dependents() -> None:
    table = _table("it_knowledge")
    store = PgKnowledgeStore(_DB_URL, table=table)  # type: ignore[arg-type]
    try:
        await store.upsert(
            ServiceRecord(
                service="checkout-service",
                namespace="prod",
                owner="payments-team",
                dependencies=["payments-db"],
                slos={"p99_latency_ms": 500},
                last_deploy="2026-07-02T10:00:00Z",
            )
        )
        await store.upsert(
            ServiceRecord(service="payments-db", namespace="prod", owner="data-team")
        )

        got = await store.get("checkout-service", namespace="prod")
        assert got is not None
        assert got.owner == "payments-team"
        assert got.dependencies == ["payments-db"]
        assert got.slos["p99_latency_ms"] == 500

        # Reverse edge via the JSONB @> containment query.
        dependents = await store.find_dependents("payments-db", namespace="prod")
        assert [r.service for r in dependents] == ["checkout-service"]

        # Upsert overwrites in place.
        await store.upsert(ServiceRecord(service="payments-db", namespace="prod", owner="dba-team"))
        again = await store.get("payments-db", namespace="prod")
        assert again is not None and again.owner == "dba-team"
    finally:
        await _drop(store, table)
        await store.aclose()
