"""Cluster knowledge graph — store, ingest, and retriever unit tests (Phase 3 W4)."""

from __future__ import annotations

import pytest
from kubepilot_orch.knowledge import (
    InMemoryKnowledgeStore,
    KnowledgeRetriever,
    ServiceRecord,
    ingest_snapshot,
    records_from_snapshot,
)
from kubepilot_orch.knowledge.ingest import OWNER_ANNOTATION

# A small fixture cluster: checkout depends on payments-db + inventory; web-frontend
# depends on checkout. Ownership comes from three different sources to exercise all
# resolution paths (explicit field / owner_map / annotation).
_SNAPSHOT = {
    "services": [
        {
            "service": "checkout-service",
            "namespace": "prod",
            "annotations": {OWNER_ANNOTATION: "payments-team"},
            "dependencies": ["payments-db"],
            "slos": {"p99_latency_ms": 500, "availability": 0.999},
            "last_deploy": "2026-07-02T10:00:00Z",
            "notes": "Owns the checkout critical path.",
        },
        {
            "service": "payments-db",
            "namespace": "prod",
            "owner": "data-team",  # explicit field
        },
        {
            "service": "inventory-service",
            "namespace": "prod",
            # owner via owner_map
        },
        {
            "service": "web-frontend",
            "namespace": "prod",
            "dependencies": ["checkout-service"],
            "owner": "web-team",
        },
    ],
    # Discovered edge (from the Tracing service_dependency_map), merged into deps.
    "dependency_map": {"checkout-service": ["inventory-service"]},
}


def test_records_from_snapshot_resolves_owners_and_merges_deps() -> None:
    records = {
        r.service: r
        for r in records_from_snapshot(_SNAPSHOT, owner_map={"inventory-service": "supply-team"})
    }

    # Ownership resolved from all three sources.
    assert records["checkout-service"].owner == "payments-team"  # annotation
    assert records["payments-db"].owner == "data-team"  # explicit field
    assert records["inventory-service"].owner == "supply-team"  # owner_map

    # Declared dep + discovered edge are unioned (order-preserving, deduped).
    assert records["checkout-service"].dependencies == ["payments-db", "inventory-service"]
    assert records["checkout-service"].slos["p99_latency_ms"] == 500


def test_dependents_are_computed_by_inversion() -> None:
    records = {r.service: r for r in records_from_snapshot(_SNAPSHOT)}
    # checkout depends on payments-db → payments-db is depended on by checkout.
    assert records["payments-db"].dependents == ["checkout-service"]
    assert records["inventory-service"].dependents == ["checkout-service"]
    # web-frontend depends on checkout → checkout's dependents include web-frontend.
    assert records["checkout-service"].dependents == ["web-frontend"]


@pytest.mark.asyncio
async def test_ingest_and_query_owner_and_deps() -> None:
    """W4 acceptance: ingest a fixture cluster, then query owner/deps of a service."""
    store = InMemoryKnowledgeStore()
    n = await ingest_snapshot(store, _SNAPSHOT, owner_map={"inventory-service": "supply-team"})
    assert n == 4
    assert len(store) == 4

    checkout = await store.get("checkout-service", namespace="prod")
    assert checkout is not None
    assert checkout.owner == "payments-team"
    assert "payments-db" in checkout.dependencies

    # Reverse edge query: what rides on payments-db?
    dependents = await store.find_dependents("payments-db", namespace="prod")
    assert [r.service for r in dependents] == ["checkout-service"]


@pytest.mark.asyncio
async def test_retriever_returns_target_plus_dependencies() -> None:
    store = InMemoryKnowledgeStore()
    await ingest_snapshot(store, _SNAPSHOT)
    retriever = KnowledgeRetriever(store)

    facts = await retriever.retrieve(service="checkout-service", namespace="prod")
    services = [f.service for f in facts]
    # Target first, then its direct dependencies (both present in the graph).
    assert services[0] == "checkout-service"
    assert set(services) == {"checkout-service", "payments-db", "inventory-service"}

    target = facts[0]
    assert target.owner == "payments-team"
    assert target.dependents == ["web-frontend"]


@pytest.mark.asyncio
async def test_retriever_misses_gracefully() -> None:
    retriever = KnowledgeRetriever(InMemoryKnowledgeStore())
    assert await retriever.retrieve(service="unknown", namespace="prod") == []
    assert await retriever.retrieve(service=None, namespace="prod") == []
    assert await retriever.get("unknown", namespace="prod") is None


@pytest.mark.asyncio
async def test_upsert_overwrites_same_key() -> None:
    store = InMemoryKnowledgeStore()
    await store.upsert(ServiceRecord(service="s", namespace="prod", owner="old"))
    await store.upsert(ServiceRecord(service="s", namespace="prod", owner="new"))
    rec = await store.get("s", namespace="prod")
    assert rec is not None
    assert rec.owner == "new"
    assert len(store) == 1


@pytest.mark.asyncio
async def test_retriever_ingest_populates_and_queries() -> None:
    """The retriever.ingest convenience is the startup/CronJob population path."""
    retriever = KnowledgeRetriever(InMemoryKnowledgeStore())
    n = await retriever.ingest(_SNAPSHOT, owner_map={"inventory-service": "supply-team"})
    assert n == 4
    facts = await retriever.retrieve(service="checkout-service", namespace="prod")
    assert {f.service for f in facts} == {"checkout-service", "payments-db", "inventory-service"}


def test_ingest_cli_dry_run_reports_count(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """`python -m ...ingest_cli` with no DB does an in-memory dry run and reports."""
    import json

    from kubepilot_orch.knowledge import ingest_cli

    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps(_SNAPSHOT), encoding="utf-8")

    rc = ingest_cli.main([str(snap)])  # no --db-url → in-memory dry run
    assert rc == 0
    assert "ingested 4 services" in capsys.readouterr().out
