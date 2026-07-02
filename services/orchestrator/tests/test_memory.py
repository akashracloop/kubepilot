"""Tests for the long-term memory subsystem (embedder, store, retriever)."""

from __future__ import annotations

import uuid

import pytest
from kubepilot_orch.memory import (
    HashEmbedder,
    InMemoryMemoryStore,
    MemoryRetriever,
    StoredIncident,
)
from kubepilot_orch.memory.store import cosine_similarity


async def test_hash_embedder_is_deterministic_and_normalized() -> None:
    emb = HashEmbedder(dim=128)
    a1, a2 = await emb.embed(["OOMKilled memory leak restart"] * 2)
    assert a1 == a2  # deterministic
    assert cosine_similarity(a1, a1) == pytest.approx(1.0)  # unit length


async def test_hash_embedder_similar_text_scores_higher() -> None:
    emb = HashEmbedder(dim=256)
    query, similar, different = await emb.embed(
        [
            "payment-service OOMKilled java heap memory",
            "payment-service OOMKilled java heap exhaustion memory",
            "dns resolution failure networkpolicy blocked egress",
        ]
    )
    assert cosine_similarity(query, similar) > cosine_similarity(query, different)


async def test_in_memory_store_returns_nearest_first() -> None:
    emb = HashEmbedder(dim=256)
    store = InMemoryMemoryStore()
    for text, svc in [
        ("java heap OOMKilled payment-service", "payment-service"),
        ("go panic nil pointer checkout-service", "checkout-service"),
    ]:
        (vec,) = await emb.embed([text])
        await store.add(
            StoredIncident(incident_id=uuid.uuid4(), summary=text, embedding=vec, service=svc)
        )
    (q,) = await emb.embed(["java heap memory OOMKilled"])
    results = await store.search(q, k=2)
    assert len(results) == 2
    assert results[0][0].service == "payment-service"  # nearest
    assert results[0][1] >= results[1][1]  # sorted by similarity desc


async def test_store_namespace_filter() -> None:
    emb = HashEmbedder(dim=64)
    store = InMemoryMemoryStore()
    (v,) = await emb.embed(["x"])
    await store.add(
        StoredIncident(incident_id=uuid.uuid4(), summary="a", embedding=v, namespace="prod")
    )
    await store.add(
        StoredIncident(incident_id=uuid.uuid4(), summary="b", embedding=v, namespace="staging")
    )
    prod = await store.search(v, namespace="prod", k=5)
    assert len(prod) == 1
    assert prod[0][0].namespace == "prod"


async def test_retriever_index_then_retrieve_with_service_boost() -> None:
    retriever = MemoryRetriever(HashEmbedder(dim=256), InMemoryMemoryStore())

    # A near-identical past incident on the SAME service, and a somewhat-similar one elsewhere.
    await retriever.index(
        incident_id=uuid.uuid4(),
        summary="checkout-service latency regression from an N+1 query after a deploy",
        root_cause_category="LatencyRegression",
        namespace="prod",
        service="checkout-service",
        outcome="reverted the query change",
    )
    await retriever.index(
        incident_id=uuid.uuid4(),
        summary="orders-service latency regression from a slow downstream call",
        root_cause_category="LatencyRegression",
        namespace="prod",
        service="orders-service",
    )

    hits = await retriever.retrieve(
        query_summary="checkout-service slow: latency regression, likely a recent deploy",
        namespace="prod",
        service="checkout-service",
        root_cause_category="LatencyRegression",
        k=2,
    )
    assert hits, "expected at least one retrieved incident"
    assert hits[0].service == "checkout-service"  # same-service boost wins
    assert hits[0].outcome == "reverted the query change"
    assert 0.0 <= hits[0].similarity <= 1.0


# ---- Hybrid (dense + lexical) retrieval (Phase 2 refinement) ---------------

from kubepilot_orch.memory.store import lexical_overlap  # noqa: E402


def test_lexical_overlap_jaccard() -> None:
    assert lexical_overlap("oom killed java heap", "why was it oom killed") == pytest.approx(2 / 7)
    assert lexical_overlap("", "anything") == 0.0
    assert lexical_overlap("disk pressure", "network partition") == 0.0


@pytest.mark.asyncio
async def test_hybrid_lexical_breaks_dense_ties() -> None:
    """With equal dense scores, the lexically-matching incident ranks first."""
    store = InMemoryMemoryStore()
    emb = [1.0, 0.0, 0.0]  # identical embedding → identical dense cosine
    await store.add(
        StoredIncident(
            incident_id=uuid.uuid4(),
            summary="disk pressure on node",
            embedding=emb,
            namespace="prod",
        )
    )
    await store.add(
        StoredIncident(
            incident_id=uuid.uuid4(),
            summary="oom killed java heap",
            embedding=emb,
            namespace="prod",
        )
    )
    hybrid = await store.search(emb, k=2, query_text="why was the container oom killed")
    assert hybrid[0][0].summary == "oom killed java heap"
    assert hybrid[0][1] > hybrid[1][1]  # lexical term broke the tie

    # Without query_text the score is pure dense (a tie here) — both returned.
    plain = await store.search(emb, k=2)
    assert {r[0].summary for r in plain} == {"disk pressure on node", "oom killed java heap"}
