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

import itertools
from dataclasses import dataclass
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from kubepilot_orch import timeline
from kubepilot_orch.agents import (
    critic_agent,
    deployment_agent,
    finalize,
    knowledge_agent,
    kubernetes_agent,
    logs_agent,
    memory_agent,
    metrics_agent,
    rca_agent,
    recommendation_agent,
    supervisor,
    tracing_agent,
)
from kubepilot_orch.agents.prompt_registry import resolve_prompt
from kubepilot_orch.calibration import IsotonicCalibrator
from kubepilot_orch.knowledge.retriever import KnowledgeRetriever
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
    # Phase 3 cluster knowledge graph. When present, a knowledge node runs before
    # RCA (beside memory) and injects owner/dependency/SLO context into
    # state.knowledge_context.
    knowledge: KnowledgeRetriever | None = None
    # Phase 3 confidence calibrator (fit from eval history). When present + fitted,
    # finalize maps the raw RCA confidence to an empirically-calibrated value,
    # overriding the critic's interim calibrated_confidence.
    calibrator: IsotonicCalibrator | None = None
    # Phase 3 critic. When True, a critic node runs between RCA and recommendation,
    # producing an independent agreement score, a critic-adjusted confidence, and an
    # escalate-to-human flag. Off by default so the minimal graph is unchanged; the
    # api-gateway turns it on via config.
    enable_critic: bool = False
    # Optional LLM pass to polish timeline labels at finalize (ordering untouched).
    # Off by default — deterministic labels are the reliable baseline.
    timeline_llm_labels: bool = False


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

    async def knowledge_node(state: InvestigationState) -> dict[str, Any]:
        assert deps.knowledge is not None  # node only added when present
        facts = await knowledge_agent.run(state, retriever=deps.knowledge)
        return knowledge_agent.to_state_update(facts)

    async def rca_node(state: InvestigationState) -> dict[str, Any]:
        report = await rca_agent.run(state, llm=deps.llm)
        update = rca_agent.to_state_update(report)
        update["prompt_versions"] = _prompt_version(
            rca_agent.AGENT_NAME, rca_agent.PROMPT_NAME, state
        )
        return update

    async def critic_node(state: InvestigationState) -> dict[str, Any]:
        critique = await critic_agent.run(state, llm=deps.llm)
        update = critic_agent.to_state_update(critique)
        update["prompt_versions"] = _prompt_version(
            critic_agent.AGENT_NAME, critic_agent.PROMPT_NAME, state
        )
        return update

    async def finalize_node(state: InvestigationState) -> dict[str, Any]:
        update = await finalize.finalize_node(state)
        # Empirically calibrate the raw RCA confidence when a fitted calibrator is
        # wired in. This is the authoritative calibrated_confidence — it overrides
        # the critic's interim value (W2), which only tempered by evidence gaps.
        if deps.calibrator is not None and deps.calibrator.is_fitted and state.rca is not None:
            update["calibrated_confidence"] = deps.calibrator.calibrate(state.rca.confidence)
        # Optional LLM polish of timeline labels (ordering untouched, fails open).
        if deps.timeline_llm_labels and update.get("timeline"):
            update["timeline"] = await timeline.refine_labels(update["timeline"], llm=deps.llm)
        if deps.memory is not None:
            # Index the concluded incident so future investigations can recall it.
            await memory_agent.index_incident(state, retriever=deps.memory)
        return update

    async def recommendation_node(state: InvestigationState) -> dict[str, Any]:
        recs = await recommendation_agent.run(state, llm=deps.llm)
        update = recommendation_agent.to_state_update(recs)
        update["prompt_versions"] = _prompt_version(
            recommendation_agent.AGENT_NAME, recommendation_agent.PROMPT_NAME, state
        )
        return update

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

    # Pre-RCA context collectors run between the specialist fan-in and RCA, each
    # feeding a slice of corroborating context into state before RCA reasons:
    #   - Phase 2 memory     → similar past incidents (state.memory_context)
    #   - Phase 3 knowledge  → cluster graph: owner/deps/SLOs (state.knowledge_context)
    # They run as a SERIAL chain (memory → knowledge → rca), NOT in parallel: both
    # collectors write the singleton ``current_step`` field, and LangGraph rejects
    # two concurrent writes to a non-reducer field. Serial is cheap here (these are
    # fast retrievals, not LLM calls). With none present the specialists feed RCA
    # directly (the Phase 1 shape, byte-for-byte unchanged).
    pre_rca: list[str] = []
    if deps.memory is not None:
        graph.add_node("memory", memory_node)
        pre_rca.append("memory")
    if deps.knowledge is not None:
        graph.add_node("knowledge", knowledge_node)
        pre_rca.append("knowledge")

    # START → supervisor → fan out to all specialists → fan in to the first
    # collector → serial collector chain → rca.
    graph.add_edge(START, "supervisor")
    fan_in = pre_rca[0] if pre_rca else "rca"
    for name in specialists:
        graph.add_edge("supervisor", name)
        graph.add_edge(name, fan_in)
    for earlier, later in itertools.pairwise(pre_rca):
        graph.add_edge(earlier, later)
    if pre_rca:
        graph.add_edge(pre_rca[-1], "rca")

    # Phase 3 critic: an adversarial review between RCA and recommendation. When
    # enabled, rca → critic → recommendation; otherwise rca → recommendation.
    if deps.enable_critic:
        graph.add_node("critic", critic_node)
        graph.add_edge("rca", "critic")
        graph.add_edge("critic", "recommendation")
    else:
        graph.add_edge("rca", "recommendation")
    # recommendation → finalize → END
    graph.add_edge("recommendation", "finalize")
    graph.add_edge("finalize", END)

    log.info(
        "graph_built",
        specialists=specialists,
        memory=deps.memory is not None,
        knowledge=deps.knowledge is not None,
        critic=deps.enable_critic,
        checkpointer=checkpointer is not None,
    )
    return graph.compile(checkpointer=checkpointer)


def _prompt_version(agent_name: str, prompt_name: str, state: InvestigationState) -> dict[str, str]:
    """Record which prompt version an agent used, keyed by incident id for A/B.

    Re-resolves the same deterministic version the agent used (``resolve_prompt``
    is a pure function of name + key), so ``state.prompt_versions`` traces exactly
    which arm produced this investigation.
    """
    version, _ = resolve_prompt(prompt_name, key=str(state.incident_id))
    return {agent_name: version}


def _agent_to_state_update(agent_name: str, output: AgentOutput) -> dict[str, Any]:
    """Specialist-agent state update — only fields with reducers, since these run in parallel."""
    return {
        "evidence": output.evidence,
        "agent_outputs": {agent_name: output},
        "completed_agents": [agent_name],
    }
