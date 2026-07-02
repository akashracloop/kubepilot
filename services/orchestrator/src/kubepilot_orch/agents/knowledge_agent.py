"""Knowledge agent — injects cluster-graph context before the RCA reasons (Phase 3).

Not an LLM agent: it asks the ``KnowledgeRetriever`` for the target service's
knowledge (owner, dependencies, SLOs) plus its direct dependencies, and populates
``state.knowledge_context``. Runs beside the memory node, before RCA.
"""

from __future__ import annotations

from typing import Any

import structlog

from kubepilot_orch.knowledge.retriever import KnowledgeRetriever
from kubepilot_orch.state import InvestigationState, ServiceKnowledge

log = structlog.get_logger(__name__)

AGENT_NAME = "knowledge"


async def run(
    state: InvestigationState, *, retriever: KnowledgeRetriever
) -> list[ServiceKnowledge]:
    facts = await retriever.retrieve(service=state.service, namespace=state.namespace)
    log.info("knowledge_agent_retrieved", incident_id=str(state.incident_id), facts=len(facts))
    return facts


def to_state_update(facts: list[ServiceKnowledge]) -> dict[str, Any]:
    return {
        "knowledge_context": facts,
        "current_step": "knowledge_retrieved",
        "completed_agents": [AGENT_NAME],
    }
