"""Hybrid retrieval over incident memory.

``index`` embeds a concluded incident and stores it. ``retrieve`` embeds the
current incident's summary, pulls nearest neighbours from the store, and
re-ranks with lightweight metadata boosts (same service / same namespace / same
root-cause category) before returning ``PastIncident`` objects for the RCA
prompt. The metadata boost is the "hybrid" signal on top of dense similarity;
a full BM25 term index over metadata is a later refinement.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog

from kubepilot_orch.memory.embedder import Embedder
from kubepilot_orch.memory.store import MemoryStore, StoredIncident
from kubepilot_orch.state import PastIncident

log = structlog.get_logger(__name__)


class MemoryRetriever:
    def __init__(
        self,
        embedder: Embedder,
        store: MemoryStore,
        *,
        service_boost: float = 0.10,
        category_boost: float = 0.05,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._service_boost = service_boost
        self._category_boost = category_boost

    async def index(
        self,
        *,
        incident_id: UUID,
        summary: str,
        root_cause_category: str | None = None,
        namespace: str | None = None,
        service: str | None = None,
        outcome: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        (embedding,) = await self._embedder.embed([summary])
        await self._store.add(
            StoredIncident(
                incident_id=incident_id,
                summary=summary,
                embedding=embedding,
                root_cause_category=root_cause_category,
                namespace=namespace,
                service=service,
                outcome=outcome,
                occurred_at=occurred_at,
            )
        )
        log.info("memory_indexed", incident_id=str(incident_id), service=service)

    async def retrieve(
        self,
        *,
        query_summary: str,
        namespace: str | None = None,
        service: str | None = None,
        root_cause_category: str | None = None,
        k: int = 3,
        min_similarity: float = 0.0,
    ) -> list[PastIncident]:
        (query_vec,) = await self._embedder.embed([query_summary])
        # Over-fetch, then re-rank with metadata boosts.
        candidates = await self._store.search(query_vec, namespace=namespace, k=max(k * 3, k))

        ranked: list[tuple[StoredIncident, float, float]] = []
        for incident, sim in candidates:
            score = sim
            if service and incident.service == service:
                score += self._service_boost
            if root_cause_category and incident.root_cause_category == root_cause_category:
                score += self._category_boost
            ranked.append((incident, sim, score))
        ranked.sort(key=lambda t: t[2], reverse=True)

        out: list[PastIncident] = []
        for incident, _sim, score in ranked[:k]:
            if score < min_similarity:
                continue
            out.append(
                PastIncident(
                    incident_id=incident.incident_id,
                    summary=incident.summary,
                    root_cause_category=incident.root_cause_category,
                    namespace=incident.namespace,
                    service=incident.service,
                    similarity=round(min(1.0, score), 4),
                    outcome=incident.outcome,
                    occurred_at=incident.occurred_at,
                )
            )
        log.info("memory_retrieved", count=len(out), service=service)
        return out
