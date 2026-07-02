"""Populate the cluster knowledge graph (Phase 3).

Ingestion is data-driven: a ``ClusterSnapshot`` (a plain dict assembled from
read-only sources) is turned into ``ServiceRecord`` rows and upserted. Sources the
snapshot models — none of which require cluster *writes*:

- **labels / annotations** — ``kubepilot.io/owner`` (or a values-provided owner map)
  gives ownership; other labels can carry team/tier hints.
- **ServiceMonitors / PrometheusRules** — SLO targets (latency / availability).
- **dependency discovery** — declared ``dependencies`` merged with a
  ``dependency_map`` (caller→callee edges from the Tracing agent's
  ``service_dependency_map`` and/or NetworkPolicies).

Dependents (reverse edges) are computed here by inverting every service's
dependencies across the snapshot, so a lookup can answer "what rides on
payments-db?" without a second pass.

The owner-annotation key and the ingest shape are the seam; a live controller
that watches the cluster and calls :func:`ingest_snapshot` is a later addition.
"""

from __future__ import annotations

from typing import Any

import structlog

from kubepilot_orch.knowledge.graph import KnowledgeStore, ServiceRecord

log = structlog.get_logger(__name__)

OWNER_ANNOTATION = "kubepilot.io/owner"


def _owner_for(entry: dict[str, Any], owner_map: dict[str, str]) -> str | None:
    """Resolve owner: explicit field > owner_map > owner annotation/label."""
    if entry.get("owner"):
        return str(entry["owner"])
    service = entry.get("service", "")
    if service in owner_map:
        return owner_map[service]
    labels = {**entry.get("labels", {}), **entry.get("annotations", {})}
    if OWNER_ANNOTATION in labels:
        return str(labels[OWNER_ANNOTATION])
    return None


def records_from_snapshot(
    snapshot: dict[str, Any],
    *,
    owner_map: dict[str, str] | None = None,
) -> list[ServiceRecord]:
    """Build ServiceRecords from a snapshot, merging deps and computing dependents.

    ``snapshot`` shape::

        {
          "services": [
            {"service": "checkout-service", "namespace": "prod",
             "labels": {"kubepilot.io/owner": "payments-team"},
             "dependencies": ["payments-db"], "slos": {...},
             "last_deploy": "2026-07-02T10:00:00Z", "notes": "..."},
            ...
          ],
          "dependency_map": {"checkout-service": ["inventory-service"]}  # optional
        }
    """
    owner_map = owner_map or {}
    dep_map: dict[str, list[str]] = snapshot.get("dependency_map", {})

    records: dict[tuple[str, str], ServiceRecord] = {}
    for entry in snapshot.get("services", []):
        service = entry["service"]
        namespace = entry.get("namespace", "default")
        # Union of declared deps + discovered edges, order-preserving + de-duped.
        deps = _dedupe([*entry.get("dependencies", []), *dep_map.get(service, [])])
        records[(namespace, service)] = ServiceRecord(
            service=service,
            namespace=namespace,
            owner=_owner_for(entry, owner_map),
            dependencies=deps,
            dependents=[],  # filled below
            slos=dict(entry.get("slos", {})),
            last_deploy=entry.get("last_deploy"),
            notes=entry.get("notes"),
        )

    _compute_dependents(records)
    return list(records.values())


def _compute_dependents(records: dict[tuple[str, str], ServiceRecord]) -> None:
    """Invert dependencies: if A depends on B, add A to B's dependents (same namespace)."""
    for (namespace, caller), rec in records.items():
        for callee in rec.dependencies:
            target = records.get((namespace, callee))
            if target is not None and caller not in target.dependents:
                target.dependents.append(caller)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


async def ingest_snapshot(
    store: KnowledgeStore,
    snapshot: dict[str, Any],
    *,
    owner_map: dict[str, str] | None = None,
) -> int:
    """Ingest a cluster snapshot into ``store``. Returns the number of services upserted."""
    records = records_from_snapshot(snapshot, owner_map=owner_map)
    for record in records:
        await store.upsert(record)
    log.info("knowledge_ingested", services=len(records))
    return len(records)
