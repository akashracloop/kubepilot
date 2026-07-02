"""Hybrid retrieval over incident memory.

``index`` embeds a concluded incident and stores it. ``retrieve`` embeds the
current incident's summary, pulls nearest neighbours from the store using
**hybrid ranking** — dense embedding cosine blended with a lexical term (a
Postgres ``ts_rank_cd`` full-text rank in prod, a Jaccard token overlap in the
in-memory store) — then re-ranks with lightweight metadata boosts (same service /
namespace / root-cause category) before returning ``PastIncident`` objects for
the RCA prompt.
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
        # Over-fetch with hybrid (dense + lexical) ranking, then re-rank with
        # metadata boosts. Passing query_text enables the lexical/tsvector term.
        candidates = await self._store.search(
            query_vec, namespace=namespace, k=max(k * 3, k), query_text=query_summary
        )

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
