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
    remediation_agent,
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
    # Phase 4 remediation. When True, a remediation node proposes an executable
    # plan after recommendation, and the graph **interrupts before executing** it
    # (HITL approval). Off by default — the whole write path is opt-in. Real
    # execution requires ``mcp_write`` (W7); in W5 the execute node resolves the
    # approval outcome only.
    enable_remediation: bool = False
    mcp_write: MCPClient | None = None
    # Execution policy (default-deny). Required for any action to actually run;
    # without it (or without mcp_write) the execute node resolves the approval
    # outcome but performs no writes.
    policy: Any | None = None  # RemediationPolicy | None
    # Optional signal snapshot fetch: ``(state) -> {"error_rate": .., "restarts": ..}``.
    # The execute node calls it once BEFORE writing (baseline) and once AFTER, then
    # validates the fix (W9) and auto-rolls-back reversible actions on a regression
    # (W8). Without it, the outcome is closed unless an execution failed.
    remediation_signal_fn: Any | None = None
    # Optional pre-execution state capture: ``(action) -> {"replicas": ..} | ...``.
    # Wired into the executor so an auto-rollback has an inverse to apply
    # (rollback.inverse_action). Without it, only self-inverting actions
    # (cordon↔uncordon) can be rolled back.
    pre_state_fn: Any | None = None
    # Phase 4 W10 self-healing. The set of opt-in patterns (see selfheal.PATTERNS)
    # that may execute WITHOUT interactive approval. Empty by default → the graph
    # shape is unchanged and everything routes through the HITL interrupt. When
    # non-empty AND a matching incident is found, the run routes to an autonomous
    # self-heal node instead of the interrupt (still policy/blast/kill/audit/
    # rollback gated). Requires mcp_write + policy.
    selfheal_patterns: frozenset[str] = frozenset()
    selfheal_actor_role: str = "operator"


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

    async def remediation_node(state: InvestigationState) -> dict[str, Any]:
        plan = await remediation_agent.run(state, llm=deps.llm)
        # Estimate each action's blast radius from live cluster facts BEFORE the
        # HITL interrupt, so approvers see the real impact and the policy
        # blast-radius caps (W2) have numbers to gate on.
        if plan.actions:
            from kubepilot_orch.remediation import cluster_facts

            for action in plan.actions:
                action.estimated_blast_radius = await cluster_facts.estimate_blast_radius(
                    action, deps.mcp_k8s, state.knowledge_context
                )
        update = remediation_agent.to_state_update(plan)
        update["prompt_versions"] = _prompt_version(
            remediation_agent.AGENT_NAME, remediation_agent.PROMPT_NAME, state
        )
        return update

    async def execute_remediation_node(state: InvestigationState) -> dict[str, Any]:
        # The graph interrupts BEFORE this node (interrupt_before below) so a human
        # can approve/reject; the API records the decision into the checkpointed
        # state, then the run resumes here.
        from kubepilot_orch.remediation import approval, executor

        plan = state.remediation_plan
        if plan is None or not plan.actions:
            return {"current_step": "remediation_skipped", "completed_agents": ["remediation_exec"]}
        status = approval.plan_status(plan, state.approvals, generated_at=plan.generated_at)

        # Only an approved plan with an executor + policy wired in actually runs.
        # Anything else (rejected/expired/pending, or no executor) resolves the
        # outcome without any write.
        if status != "approved" or deps.mcp_write is None or deps.policy is None:
            return {
                "remediation_outcome": status,
                "current_step": "remediation_resolved",
                "completed_agents": ["remediation_exec"],
            }

        # Capture the baseline signals just before writing, so the post-check has a
        # genuine pre-remediation comparison point (the incident's un-fixed state).
        before = await deps.remediation_signal_fn(state) if deps.remediation_signal_fn else None

        records = await executor.execute_plan(
            plan,
            state.approvals,
            mcp_write=deps.mcp_write,
            policy=deps.policy,
            pre_state_fn=deps.pre_state_fn,
        )
        update: dict[str, Any] = {
            "executions": records,
            "current_step": "remediation_executed",
            "completed_agents": ["remediation_exec"],
        }
        update.update(await _validate_execution(records, before, state))
        return update

    async def _validate_execution(
        records: list[Any], before: dict[str, float] | None, state: InvestigationState
    ) -> dict[str, Any]:
        """Resolve the outcome after a write: reopen on failure/regression, close
        on improvement, auto-rollback reversible actions on a regression (W8/W9).

        Shared by the HITL execute node and the autonomous self-heal node.
        """
        if any(r.status == "failed" for r in records):
            return {"remediation_outcome": "reopened"}
        # Validate against post-remediation signals when a signal fetcher is wired.
        if deps.remediation_signal_fn is not None and before is not None:
            from kubepilot_orch.remediation import validation

            after = await deps.remediation_signal_fn(state)
            result = await validation.finalize_remediation(
                records, before, after, mcp_write=deps.mcp_write
            )
            out: dict[str, Any] = {"remediation_outcome": result.outcome}
            if result.rollbacks:
                out["rollbacks"] = result.rollbacks
            return out
        return {"remediation_outcome": "closed"}

    async def self_heal_node(state: InvestigationState) -> dict[str, Any]:
        # Autonomous path (W10): a matched, opt-in pattern executes WITHOUT the HITL
        # interrupt but through every other gate (policy → blast cap → kill switch →
        # audit → auto-rollback). Reached only when a pattern matched at routing.
        from kubepilot_orch.remediation import selfheal

        before = await deps.remediation_signal_fn(state) if deps.remediation_signal_fn else None
        records = await selfheal.self_heal(
            state,
            enabled=deps.selfheal_patterns,
            mcp_write=deps.mcp_write,
            policy=deps.policy,
            actor_role=deps.selfheal_actor_role,
        )
        update: dict[str, Any] = {
            "executions": records,
            "current_step": "self_healed",
            "completed_agents": ["self_heal"],
        }
        if not records:  # pattern matched but policy/kill-switch skipped it
            update["remediation_outcome"] = "reopened"
            return update
        update.update(await _validate_execution(records, before, state))
        return update

    def _selfheal_route(state: InvestigationState) -> str:
        """Route a matched+enabled self-heal incident to the autonomous node,
        else to the normal HITL remediation branch."""
        from kubepilot_orch.remediation import selfheal

        if selfheal.select_action(state, deps.selfheal_patterns) is not None:
            return "self_heal"
        return "remediation"

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

    # Phase 4 remediation: recommendation → remediation → [INTERRUPT] → execute →
    # finalize. The interrupt-before-execute is the HITL approval gate. Off by
    # default, so the Phase 1-3 shape is recommendation → finalize unchanged.
    interrupt_before: list[str] = []
    if deps.enable_remediation:
        graph.add_node("remediation", remediation_node)
        graph.add_node("execute_remediation", execute_remediation_node)
        graph.add_edge("remediation", "execute_remediation")
        graph.add_edge("execute_remediation", "finalize")
        interrupt_before.append("execute_remediation")
        # W10 self-healing: when opt-in patterns are configured, route a matching
        # incident to the autonomous node (no interrupt); everything else follows
        # the HITL branch. With no patterns the recommendation → remediation edge
        # is unconditional, so the Phase-1..4 default shape is unchanged.
        if deps.selfheal_patterns:
            graph.add_node("self_heal", self_heal_node)
            graph.add_conditional_edges(
                "recommendation",
                _selfheal_route,
                {"self_heal": "self_heal", "remediation": "remediation"},
            )
            graph.add_edge("self_heal", "finalize")
        else:
            graph.add_edge("recommendation", "remediation")
    else:
        graph.add_edge("recommendation", "finalize")
    graph.add_edge("finalize", END)

    log.info(
        "graph_built",
        specialists=specialists,
        memory=deps.memory is not None,
        knowledge=deps.knowledge is not None,
        critic=deps.enable_critic,
        remediation=deps.enable_remediation,
        checkpointer=checkpointer is not None,
    )
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before or None,
    )


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
