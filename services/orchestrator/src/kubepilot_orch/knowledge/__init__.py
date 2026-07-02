"""Cluster knowledge graph (Phase 3).

A relational map of services ↔ owners ↔ dependencies ↔ SLOs, ingested from
read-only cluster sources (labels, an ownership map, ServiceMonitors, dependency
discovery) and queried before the RCA reasons. A knowledge node populates
``InvestigationState.knowledge_context``; the RCA agent weighs ownership +
dependencies + SLOs as corroborating context (never overriding live signals).

Layers mirror Phase 2 memory:
- ``graph``     — ``ServiceRecord`` + ``KnowledgeStore`` (in-memory / pgvector-pg).
- ``ingest``    — build records from a cluster snapshot, compute reverse edges.
- ``retriever`` — ``ServiceKnowledge`` for the target service + its dependencies.
"""

from __future__ import annotations

from kubepilot_orch.knowledge.graph import (
    InMemoryKnowledgeStore,
    KnowledgeStore,
    PgKnowledgeStore,
    ServiceRecord,
)
from kubepilot_orch.knowledge.ingest import ingest_snapshot, records_from_snapshot
from kubepilot_orch.knowledge.retriever import KnowledgeRetriever

__all__ = [
    "InMemoryKnowledgeStore",
    "KnowledgeRetriever",
    "KnowledgeStore",
    "PgKnowledgeStore",
    "ServiceRecord",
    "ingest_snapshot",
    "records_from_snapshot",
]
