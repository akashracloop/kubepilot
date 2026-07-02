"""Retrieve knowledge-graph context for an investigation (Phase 3).

Given the target service, return its own knowledge plus its **direct dependencies'**
knowledge, so the RCA agent can both (a) name the owning team + SLOs and (b)
correlate a dependency's failure to the target. Corroborating context only —
knowledge never overrides live signals (same discipline as long-term memory).
"""

from __future__ import annotations

from typing import Any

import structlog

from kubepilot_orch.knowledge.graph import KnowledgeStore
from kubepilot_orch.knowledge.ingest import ingest_snapshot
from kubepilot_orch.state import ServiceKnowledge

log = structlog.get_logger(__name__)


class KnowledgeRetriever:
    def __init__(self, store: KnowledgeStore, *, max_dependencies: int = 5) -> None:
        self._store = store
        self._max_dependencies = max_dependencies

    @property
    def store(self) -> KnowledgeStore:
        return self._store

    async def ingest(
        self, snapshot: dict[str, Any], *, owner_map: dict[str, str] | None = None
    ) -> int:
        """Populate the backing store from a cluster snapshot. Returns services upserted."""
        return await ingest_snapshot(self._store, snapshot, owner_map=owner_map)

    async def get(self, service: str, *, namespace: str | None = None) -> ServiceKnowledge | None:
        record = await self._store.get(service, namespace=namespace)
        return record.to_knowledge() if record else None

    async def retrieve(
        self, *, service: str | None, namespace: str | None = None
    ) -> list[ServiceKnowledge]:
        """Knowledge for ``service`` + its direct dependencies (deduped, target first)."""
        if not service:
            return []
        root = await self._store.get(service, namespace=namespace)
        if root is None:
            log.info("knowledge_miss", service=service, namespace=namespace)
            return []

        out: list[ServiceKnowledge] = [root.to_knowledge()]
        seen = {root.service}
        for dep in root.dependencies[: self._max_dependencies]:
            if dep in seen:
                continue
            seen.add(dep)
            dep_record = await self._store.get(dep, namespace=namespace)
            if dep_record is not None:
                out.append(dep_record.to_knowledge())
        log.info("knowledge_retrieved", service=service, count=len(out))
        return out
