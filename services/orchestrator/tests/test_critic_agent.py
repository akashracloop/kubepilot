"""Critic agent unit tests — adversarially review an RCAReport into a Critique.

These are scripted-LLM tests: they verify the caller-owned validation, the
deterministic escalation/adjusted-confidence policy, and the fail-open fallback.
The actual *uplift* from critique is measured by the debate eval (W3) and live
tests — ScriptedLLM bypasses provider message conversion.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from kubepilot_orch.agents import critic_agent
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.state import (
    AgentOutput,
    Critique,
    Evidence,
    InvestigationState,
    RCAReport,
    Recommendation,
    Severity,
)
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)


def _state(rca: RCAReport | None, evidence: list[Evidence] | None = None) -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        query="why is payment-service failing?",
        namespace="prod",
        service="payment-service",
        evidence=evidence or [],
        agent_outputs={"kubernetes": AgentOutput(agent_name="kubernetes", succeeded=True)},
        completed_agents=["kubernetes", "metrics", "logs", "rca"],
        rca=rca,
        confidence=rca.confidence if rca else None,
        started_at=datetime(2026, 7, 2, 10, 7, tzinfo=UTC),
    )


_STRONG_RCA = RCAReport(
    root_cause="JVM heap exhaustion: OOMKilled corroborated by memory saturation and OOM traces.",
    root_cause_category="OOMKilled",
    confidence=0.92,
    evidence_refs=[0, 1, 2],
    reasoning="Three specialists corroborate the OOM mechanism.",
    recommendations=["Roll back", "Raise memory limit"],
)

_WEAK_RCA = RCAReport(
    root_cause="Probably a network partition.",
    root_cause_category="NetworkPartition",
    confidence=0.85,
    evidence_refs=[0],
    reasoning="One log line mentioned a timeout.",
    recommendations=["Check the network"],
)

_EVIDENCE = [
    Evidence(
        source_agent="logs",
        kind="log_pattern",
        summary="one connection timeout log line",
        severity=Severity.WARNING,
        collected_at=datetime(2026, 7, 2, 10, 8, tzinfo=UTC),
    )
]


@pytest.mark.asyncio
async def test_critic_lowers_confidence_and_escalates_on_contradictory_rca() -> None:
    """A low-agreement critique tempers confidence and forces escalation."""
    scripted_critique = Critique(
        agreement=0.3,
        concerns=[
            "A single timeout log line does not establish a network partition.",
            "No metrics or k8s evidence corroborates; alternative causes not ruled out.",
        ],
        adjusted_confidence=0.35,
        escalate_to_human=False,  # model didn't escalate; policy must force it
    )
    scripted = ScriptedLLM(responses=[llm_text(scripted_critique.model_dump_json())])

    critique = await critic_agent.run(_state(_WEAK_RCA, _EVIDENCE), llm=build_router(scripted))

    assert critique.agreement == pytest.approx(0.3)
    assert critique.adjusted_confidence == pytest.approx(0.35)
    assert critique.adjusted_confidence < _WEAK_RCA.confidence
    # Low agreement (< 0.5) forces escalation even though the model said False.
    assert critique.escalate_to_human is True
    assert len(critique.concerns) == 2


@pytest.mark.asyncio
async def test_critic_backs_a_strong_rca_without_escalation() -> None:
    scripted_critique = Critique(
        agreement=0.95,
        concerns=[],
        adjusted_confidence=0.9,
        escalate_to_human=False,
    )
    scripted = ScriptedLLM(responses=[llm_text(scripted_critique.model_dump_json())])

    critique = await critic_agent.run(_state(_STRONG_RCA), llm=build_router(scripted))

    assert critique.agreement == pytest.approx(0.95)
    assert critique.adjusted_confidence == pytest.approx(0.9)
    assert critique.escalate_to_human is False
    assert critique.concerns == []


@pytest.mark.asyncio
async def test_critic_derives_adjusted_confidence_when_model_omits_it() -> None:
    """No model-supplied adjusted_confidence → temper RCA confidence by agreement."""
    scripted_critique = Critique(agreement=0.5, concerns=["some doubt"], adjusted_confidence=None)
    scripted = ScriptedLLM(responses=[llm_text(scripted_critique.model_dump_json())])

    critique = await critic_agent.run(_state(_STRONG_RCA), llm=build_router(scripted))

    # 0.92 * 0.5 = 0.46, rounded.
    assert critique.adjusted_confidence == pytest.approx(0.46)
    # 0.46 >= 0.4 confidence floor and agreement 0.5 is not < 0.5 → no escalation.
    assert critique.escalate_to_human is False


@pytest.mark.asyncio
async def test_critic_escalates_when_derived_confidence_below_floor() -> None:
    scripted_critique = Critique(agreement=0.6, concerns=[], adjusted_confidence=0.2)
    scripted = ScriptedLLM(responses=[llm_text(scripted_critique.model_dump_json())])

    critique = await critic_agent.run(_state(_STRONG_RCA), llm=build_router(scripted))

    # adjusted 0.2 < 0.4 floor forces escalation despite agreement 0.6.
    assert critique.escalate_to_human is True


@pytest.mark.asyncio
async def test_critic_fails_open_on_invalid_output() -> None:
    """Garbage from the LLM → neutral, non-escalating critique (RCA left unchanged)."""
    scripted = ScriptedLLM(responses=[llm_text("not valid json at all")])

    critique = await critic_agent.run(_state(_STRONG_RCA), llm=build_router(scripted))

    assert critique.agreement == pytest.approx(1.0)
    assert critique.escalate_to_human is False
    assert critique.adjusted_confidence is None
    assert any("failed" in c.lower() for c in critique.concerns)


@pytest.mark.asyncio
async def test_critic_handles_missing_rca() -> None:
    scripted = ScriptedLLM(responses=[])  # must not be called
    critique = await critic_agent.run(_state(None), llm=build_router(scripted))
    assert critique.agreement == pytest.approx(1.0)
    assert critique.escalate_to_human is False


@pytest.mark.asyncio
async def test_critic_user_message_presents_rca_and_evidence() -> None:
    scripted_critique = Critique(agreement=0.9, concerns=[], adjusted_confidence=0.9)
    scripted = ScriptedLLM(responses=[llm_text(scripted_critique.model_dump_json())])

    await critic_agent.run(_state(_WEAK_RCA, _EVIDENCE), llm=build_router(scripted))

    user_msg = next(m for m in scripted.calls[0]["messages"] if m.role == "user")
    assert "NetworkPartition" in user_msg.content
    assert "stated_confidence" in user_msg.content
    assert "[0]" in user_msg.content  # evidence is indexed for the critic to cite


def test_to_state_update_maps_critique_and_calibrated_confidence() -> None:
    critique = Critique(
        agreement=0.4, concerns=["c"], adjusted_confidence=0.38, escalate_to_human=True
    )
    update = critic_agent.to_state_update(critique)
    assert update["critique"] is critique
    assert update["calibrated_confidence"] == pytest.approx(0.38)
    assert update["current_step"] == "critique_completed"
    assert update["completed_agents"] == ["critic"]


# ----------------------------------------------------------------------------
# Graph-level: the critic node runs between RCA and recommendation when enabled.
# ----------------------------------------------------------------------------


def _mcp_handler(tool: str, result: object):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": tool,
                            "description": tool,
                            "parameters": {
                                "type": "object",
                                "properties": {"namespace": {"type": "string"}},
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/mcp/health":
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": result})

    return handler


def _specialist(name: str, tool: str, summary: str) -> ScriptedLLM:
    out = AgentOutput(
        agent_name=name,
        succeeded=True,
        evidence=[_ev(name, summary)],
    )
    return ScriptedLLM(
        name=name,
        responses=[
            llm_tool_call(tool, {"namespace": "prod"}, call_id=f"{name}-1"),
            llm_text("collected"),
            llm_text(out.model_dump_json()),
        ],
    )


def _ev(agent: str, summary: str) -> Evidence:
    return Evidence(
        source_agent=agent,
        kind="observation",
        summary=summary,
        severity=Severity.WARNING,
        collected_at=datetime(2026, 7, 2, 10, 8, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_graph_runs_critic_between_rca_and_recommendation() -> None:
    """With enable_critic=True the critic node runs, sets calibrated_confidence,
    escalates a shaky RCA, and its concerns reach the recommendation prompt."""
    weak_rca = RCAReport(
        root_cause="Probably a network partition.",
        root_cause_category="NetworkPartition",
        confidence=0.85,
        evidence_refs=[0],
        reasoning="One timeout log line.",
        recommendations=["Check the network"],
    )
    critique = Critique(
        agreement=0.3,
        concerns=["A single timeout does not prove a partition."],
        adjusted_confidence=0.3,
        escalate_to_human=False,
    )
    recs = ScriptedLLM(
        name="recommendation",
        responses=[
            llm_text(
                json.dumps(
                    {
                        "recommendations": [
                            Recommendation(
                                title="Verify connectivity before acting",
                                rationale="Critic flagged the partition as unproven.",
                                commands=["kubectl get endpoints -n prod"],
                                priority=1,
                            ).model_dump()
                        ]
                    }
                )
            )
        ],
    )
    rca = ScriptedLLM(name="rca", responses=[llm_text(weak_rca.model_dump_json())])
    critic = ScriptedLLM(name="critic", responses=[llm_text(critique.model_dump_json())])

    by_keyword = [
        ("Kubernetes specialist", _specialist("kubernetes", "list_pods", "one timeout")),
        ("metrics specialist", _specialist("metrics", "query_metrics", "nominal")),
        ("logs specialist", _specialist("logs", "query_logs", "one timeout log line")),
        ("Root-Cause Analysis", rca),
        ("Critic agent", critic),
        ("Recommendation agent", recs),
    ]
    rec_calls: list[str] = []

    class Dispatcher:
        name = "dispatcher"

        async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
            sys = next((m.content for m in messages if m.role == "system"), "")
            for keyword, llm in by_keyword:
                if keyword in sys:
                    if keyword == "Recommendation agent":
                        rec_calls.append("\n".join(m.content for m in messages if m.role == "user"))
                    return await llm.chat(messages, **kwargs)
            raise AssertionError(f"No scripted LLM matched: {sys[:80]!r}")

    deps = AgentDeps(
        llm=build_router(Dispatcher()),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_mcp_handler("list_pods", {"pods": []}), server_name="mcp-k8s"),
        mcp_prom=build_mcp_client(_mcp_handler("query_metrics", {"v": 1}), server_name="mcp-prom"),
        mcp_loki=build_mcp_client(
            _mcp_handler("query_logs", {"lines": []}), server_name="mcp-loki"
        ),
        enable_critic=True,
    )

    try:
        graph = build_graph(deps)
        assert "critic" in set(graph.get_graph().nodes)
        final = await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": "why is payment-service failing?",
                "namespace": "prod",
                "service": "payment-service",
                "started_at": datetime(2026, 7, 2, 10, 7, tzinfo=UTC),
            }
        )
    finally:
        await deps.mcp_k8s.aclose()
        await deps.mcp_prom.aclose()
        await deps.mcp_loki.aclose()

    state = InvestigationState.model_validate(final)
    assert "critic" in state.completed_agents
    assert state.critique is not None
    assert state.critique.escalate_to_human is True  # policy forced it (agreement 0.3)
    # Raw RCA confidence preserved; critic-adjusted value surfaced separately.
    assert state.confidence == pytest.approx(0.85)
    assert state.calibrated_confidence == pytest.approx(0.3)
    # The critic's concern reached the recommendation prompt.
    assert rec_calls and "Critic's concerns" in rec_calls[0]


@pytest.mark.asyncio
async def test_graph_omits_critic_node_when_disabled() -> None:
    deps = AgentDeps(
        llm=build_router(ScriptedLLM(name="noop")),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_mcp_handler("list_pods", {}), server_name="mcp-k8s"),
        mcp_prom=build_mcp_client(_mcp_handler("query_metrics", {}), server_name="mcp-prom"),
        mcp_loki=build_mcp_client(_mcp_handler("query_logs", {}), server_name="mcp-loki"),
    )
    try:
        graph = build_graph(deps)
        assert "critic" not in set(graph.get_graph().nodes)
    finally:
        await deps.mcp_k8s.aclose()
        await deps.mcp_prom.aclose()
        await deps.mcp_loki.aclose()
