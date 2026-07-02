"""LangGraph wiring for the Phase-1 investigation workflow.

Pipeline:

    START
      ↓
    supervisor              (plan: stamp "investigating", in P3 conditionally route)
      ↓
      ├──> kubernetes ──┐
      ├──> metrics    ──┤   (fan-out: 3 specialists run in parallel)
      └──> logs       ──┘
                         ↓
                       rca         (fan-in: waits for all 3, correlates into RCAReport)
                         ↓
                    recommendation (enriches RCA text recs into structured commands)
                         ↓
                      finalize     (stamps finished_at + confidence + "completed")
                         ↓
                        END

LangGraph fan-in semantics: a node with multiple incoming edges runs once,
after all its predecessors complete. That's why ``rca`` runs exactly once
after the three specialists, even though there are three edges into it.

Reducer-merged state fields (evidence / agent_outputs / completed_agents)
combine parallel updates without overwrites; singleton fields (current_step,
finished_at, confidence, rca, recommendations) are owned by serial nodes only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from kubepilot_orch.agents import (
    deployment_agent,
    finalize,
    kubernetes_agent,
    logs_agent,
    memory_agent,
    metrics_agent,
    rca_agent,
    recommendation_agent,
    supervisor,
    tracing_agent,
)
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.memory.retriever import MemoryRetriever
from kubepilot_orch.state import AgentOutput, InvestigationState

log = structlog.get_logger(__name__)


@dataclass
class AgentDeps:
    """Runtime dependencies injected into agent nodes via closure.

    ``mcp_tempo`` / ``mcp_ci`` are Phase 2 and optional: when present, the
    Tracing / Deployment specialist branches are added to the graph; when None
    (Tempo / CI not deployed), the graph is the Phase 1 three-specialist shape.
    """

    llm: LLMRouter
    mcp_k8s: MCPClient
    mcp_prom: MCPClient
    mcp_loki: MCPClient
    mcp_tempo: MCPClient | None = None
    mcp_ci: MCPClient | None = None
    # Phase 2 long-term memory. When present, a retrieval node runs before RCA and
    # the concluded incident is indexed at finalize.
    memory: MemoryRetriever | None = None


def build_graph(deps: AgentDeps, *, checkpointer: Any | None = None) -> Any:
    """Build and compile the Phase-1 investigation graph.

    ``checkpointer`` is an optional LangGraph checkpointer (e.g. a Postgres
    ``AsyncPostgresSaver`` in prod, or ``MemorySaver`` in dev). When supplied,
    every ``astream``/``ainvoke`` call must pass a ``thread_id`` in its config so
    the investigation's state is persisted and resumable across pod restarts.
    When ``None`` (the default, used by unit tests), the graph runs without
    persistence — identical to prior behaviour.
    """

    async def k8s_node(state: InvestigationState) -> dict[str, Any]:
        output = await kubernetes_agent.run(
            query=state.query,
            namespace=state.namespace,
            service=state.service,
            llm=deps.llm,
            mcp_k8s=deps.mcp_k8s,
        )
        return _agent_to_state_update(kubernetes_agent.AGENT_NAME, output)

    async def metrics_node(state: InvestigationState) -> dict[str, Any]:
        output = await metrics_agent.run(
            query=state.query,
            namespace=state.namespace,
            service=state.service,
            time_window_minutes=state.time_window_minutes,
            llm=deps.llm,
            mcp_prom=deps.mcp_prom,
        )
        return _agent_to_state_update(metrics_agent.AGENT_NAME, output)

    async def logs_node(state: InvestigationState) -> dict[str, Any]:
        output = await logs_agent.run(
            query=state.query,
            namespace=state.namespace,
            service=state.service,
            time_window_minutes=state.time_window_minutes,
            llm=deps.llm,
            mcp_loki=deps.mcp_loki,
        )
        return _agent_to_state_update(logs_agent.AGENT_NAME, output)

    async def tracing_node(state: InvestigationState) -> dict[str, Any]:
        assert deps.mcp_tempo is not None  # node only added when present
        output = await tracing_agent.run(
            query=state.query,
            namespace=state.namespace,
            service=state.service,
            time_window_minutes=state.time_window_minutes,
            llm=deps.llm,
            mcp_tempo=deps.mcp_tempo,
        )
        return _agent_to_state_update(tracing_agent.AGENT_NAME, output)

    async def deployment_node(state: InvestigationState) -> dict[str, Any]:
        assert deps.mcp_ci is not None  # node only added when present
        output = await deployment_agent.run(
            query=state.query,
            namespace=state.namespace,
            service=state.service,
            time_window_minutes=state.time_window_minutes,
            llm=deps.llm,
            mcp_ci=deps.mcp_ci,
        )
        return _agent_to_state_update(deployment_agent.AGENT_NAME, output)

    async def memory_node(state: InvestigationState) -> dict[str, Any]:
        assert deps.memory is not None  # node only added when present
        incidents = await memory_agent.run(state, retriever=deps.memory)
        return memory_agent.to_state_update(incidents)

    async def rca_node(state: InvestigationState) -> dict[str, Any]:
        report = await rca_agent.run(state, llm=deps.llm)
        return rca_agent.to_state_update(report)

    async def finalize_node(state: InvestigationState) -> dict[str, Any]:
        update = await finalize.finalize_node(state)
        if deps.memory is not None:
            # Index the concluded incident so future investigations can recall it.
            await memory_agent.index_incident(state, retriever=deps.memory)
        return update

    async def recommendation_node(state: InvestigationState) -> dict[str, Any]:
        recs = await recommendation_agent.run(state, llm=deps.llm)
        return recommendation_agent.to_state_update(recs)

    graph = StateGraph(InvestigationState)

    graph.add_node("supervisor", supervisor.supervisor_node)
    graph.add_node("kubernetes", k8s_node)
    graph.add_node("metrics", metrics_node)
    graph.add_node("logs", logs_node)
    graph.add_node("rca", rca_node)
    graph.add_node("recommendation", recommendation_node)
    graph.add_node("finalize", finalize_node)

    # Core Phase 1 specialists — always present.
    specialists = ["kubernetes", "metrics", "logs"]

    # Phase 2 specialists — added only when their MCP server is wired in.
    if deps.mcp_tempo is not None:
        graph.add_node("tracing", tracing_node)
        specialists.append("tracing")
    if deps.mcp_ci is not None:
        graph.add_node("deployment", deployment_node)
        specialists.append("deployment")

    # Phase 2 memory: a retrieval node between the specialist fan-in and RCA, so
    # the RCA agent sees similar past incidents. Without memory, specialists → rca.
    fan_in = "rca"
    if deps.memory is not None:
        graph.add_node("memory", memory_node)
        graph.add_edge("memory", "rca")
        fan_in = "memory"

    # START → supervisor → fan out to all specialists → fan in.
    graph.add_edge(START, "supervisor")
    for name in specialists:
        graph.add_edge("supervisor", name)
        graph.add_edge(name, fan_in)
    # rca → recommendation → finalize → END
    graph.add_edge("rca", "recommendation")
    graph.add_edge("recommendation", "finalize")
    graph.add_edge("finalize", END)

    log.info(
        "graph_built",
        specialists=specialists,
        memory=deps.memory is not None,
        checkpointer=checkpointer is not None,
    )
    return graph.compile(checkpointer=checkpointer)


def _agent_to_state_update(agent_name: str, output: AgentOutput) -> dict[str, Any]:
    """Specialist-agent state update — only fields with reducers, since these run in parallel."""
    return {
        "evidence": output.evidence,
        "agent_outputs": {agent_name: output},
        "completed_agents": [agent_name],
    }
