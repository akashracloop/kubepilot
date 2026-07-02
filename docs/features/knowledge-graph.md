# Cluster knowledge graph

A relational map of **services ↔ owners ↔ dependencies ↔ SLOs** that gives the RCA
agent context the live signals alone don't carry: who owns a service, what it
depends on, what depends on it, and its SLO targets. Corroborating context only —
knowledge never overrides current signals (same discipline as long-term memory).

## What it stores

Per service (`ServiceRecord`): namespace, owner, dependencies, dependents, SLOs,
last deploy, notes. Two stores mirror the Phase 2 memory seam:

- `InMemoryKnowledgeStore` — dict-backed, for dev / tests.
- `PgKnowledgeStore` — Postgres-backed (JSONB deps/dependents/slos), reusing the
  bundled Postgres. Schema is created on first use.

The primary query is an exact `(namespace, service)` lookup plus a reverse
`find_dependents` ("what rides on payments-db?").

## Ingestion

`knowledge/ingest.py` turns a **read-only cluster snapshot** into records:

- **owner** from an explicit field, a values-provided owner map, or the
  `kubepilot.io/owner` label/annotation;
- **dependencies** from declared deps unioned with a discovered `dependency_map`
  (the Tracing agent's `service_dependency_map` and/or NetworkPolicies);
- **dependents** computed by inverting every service's dependencies;
- **SLOs** from ServiceMonitors/PrometheusRules.

```python
from kubepilot_orch.knowledge import InMemoryKnowledgeStore, ingest_snapshot
store = InMemoryKnowledgeStore()
await ingest_snapshot(store, snapshot, owner_map={"inventory-service": "supply-team"})
```

## In the loop

When a `KnowledgeRetriever` is wired in, a **knowledge node** runs before RCA
(beside the memory node) and populates `state.knowledge_context` with the target
service's knowledge plus its direct dependencies. The RCA prompt then names the
owning team (so the right people are paged), treats a listed dependency as a prime
suspect when its own signals look unhealthy, and notes SLO breaches.

Enable with `knowledge_enabled` (off until the graph is populated by ingestion; an
empty graph would just add a no-op node). A live controller that watches the
cluster and calls `ingest_snapshot` is a later addition; the ingest shape is the
seam.
