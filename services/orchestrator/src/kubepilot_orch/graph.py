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
    finalize,
    kubernetes_agent,
    logs_agent,
    metrics_agent,
    rca_agent,
    recommendation_agent,
    supervisor,
)
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.state import AgentOutput, InvestigationState

log = structlog.get_logger(__name__)


@dataclass
class AgentDeps:
    """Runtime dependencies injected into agent nodes via closure."""

    llm: LLMRouter
    mcp_k8s: MCPClient
    mcp_prom: MCPClient
    mcp_loki: MCPClient


def build_graph(deps: AgentDeps) -> Any:
    """Build and compile the Phase-1 investigation graph."""

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

    async def rca_node(state: InvestigationState) -> dict[str, Any]:
        report = await rca_agent.run(state, llm=deps.llm)
        return rca_agent.to_state_update(report)

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
    graph.add_node("finalize", finalize.finalize_node)

    # START → supervisor
    graph.add_edge(START, "supervisor")
    # supervisor → fan out to 3 specialists
    graph.add_edge("supervisor", "kubernetes")
    graph.add_edge("supervisor", "metrics")
    graph.add_edge("supervisor", "logs")
    # 3 specialists → fan in at rca (waits for all three)
    graph.add_edge("kubernetes", "rca")
    graph.add_edge("metrics", "rca")
    graph.add_edge("logs", "rca")
    # rca → recommendation → finalize → END
    graph.add_edge("rca", "recommendation")
    graph.add_edge("recommendation", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


def _agent_to_state_update(agent_name: str, output: AgentOutput) -> dict[str, Any]:
    """Specialist-agent state update — only fields with reducers, since these run in parallel."""
    return {
        "evidence": output.evidence,
        "agent_outputs": {agent_name: output},
        "completed_agents": [agent_name],
    }
