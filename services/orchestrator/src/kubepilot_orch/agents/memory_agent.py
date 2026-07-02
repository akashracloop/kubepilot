"""Memory agent — retrieves similar past incidents before the RCA reasons, and
indexes the concluded incident afterwards (Phase 2).

Not an LLM agent: it builds a query from the current investigation, asks the
``MemoryRetriever`` for similar past incidents, and populates
``state.memory_context``. Indexing (embed + store) happens at finalize so a
future investigation can recall this one.
"""

from __future__ import annotations

from typing import Any

import structlog

from kubepilot_orch.memory.retriever import MemoryRetriever
from kubepilot_orch.state import InvestigationState, PastIncident

log = structlog.get_logger(__name__)

AGENT_NAME = "memory"

_MAX_EVIDENCE_IN_QUERY = 8


def build_query(state: InvestigationState) -> str:
    """A retrieval query from the investigation: the question + top evidence summaries."""
    parts = [state.query]
    if state.service:
        parts.append(f"service={state.service}")
    for ev in state.evidence[:_MAX_EVIDENCE_IN_QUERY]:
        parts.append(f"{ev.kind}: {ev.summary}")
    return " | ".join(parts)


def incident_summary(state: InvestigationState) -> str:
    """The text stored for this incident (query + root cause), used for future recall."""
    parts = [state.query]
    if state.service:
        parts.append(f"service={state.service}")
    if state.rca is not None:
        parts.append(f"root_cause: {state.rca.root_cause}")
        if state.rca.root_cause_category:
            parts.append(f"category: {state.rca.root_cause_category}")
    return " | ".join(parts)


async def run(
    state: InvestigationState, *, retriever: MemoryRetriever, k: int = 3
) -> list[PastIncident]:
    hits = await retriever.retrieve(
        query_summary=build_query(state),
        namespace=state.namespace,
        service=state.service,
        k=k,
    )
    log.info("memory_agent_retrieved", incident_id=str(state.incident_id), hits=len(hits))
    return hits


def to_state_update(incidents: list[PastIncident]) -> dict[str, Any]:
    return {
        "memory_context": incidents,
        "current_step": "memory_retrieved",
        "completed_agents": [AGENT_NAME],
    }


async def index_incident(state: InvestigationState, *, retriever: MemoryRetriever) -> None:
    """Persist the concluded incident so future investigations can recall it."""
    if state.rca is None:
        return
    await retriever.index(
        incident_id=state.incident_id,
        summary=incident_summary(state),
        root_cause_category=state.rca.root_cause_category,
        namespace=state.namespace,
        service=state.service,
        outcome=None,
        occurred_at=state.started_at,
    )
