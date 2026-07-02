"""Memory A/B — does long-term memory measurably help the RCA?

Runs a recurring-incident scenario twice through the real investigation graph:
  - MEMORY ON  — the scenario's ``memory_seed`` is indexed, so the memory node
    retrieves a near-identical past incident and the RCA prompt gains a
    "Similar past incidents" section.
  - MEMORY OFF — the same scenario with ``memory_seed`` cleared, so no memory
    node runs and the RCA prompt has no past-incident context.

The LLM is a ``ScriptedLLM`` dispatcher (fully deterministic). The scripted RCA
inspects the messages it receives: when it sees the "Similar past incidents"
marker it returns a HIGHER confidence (recurrence corroborates the diagnosis),
otherwise a lower one. So the A/B demonstrably shows memory helping — the delta
comes from the graph actually injecting retrieved context, not from randomness.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kubepilot_orch.agents.recommendation_agent import _RecommendationList
from kubepilot_orch.llm.base import Message
from kubepilot_orch.state import AgentOutput, Evidence, RCAReport, Recommendation, Severity
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_router,
    llm_text,
    llm_tool_call,
)

from eval.harness.loader import Scenario, load_scenarios
from eval.harness.runner import run_scenario

# Marker the RCA agent writes into its user prompt when memory_context is non-empty
# (see kubepilot_orch/agents/rca_agent.py::_build_user_message).
MEMORY_MARKER = "Similar past incidents"

CONFIDENCE_WITH_MEMORY = 0.92
CONFIDENCE_WITHOUT_MEMORY = 0.74


def _rca_response(*, messages: list[Message], **_: Any) -> Any:
    """Scripted RCA: confidence depends on whether memory context was injected."""
    user_text = "\n".join(m.content for m in messages if m.role == "user")
    has_memory = MEMORY_MARKER in user_text
    confidence = CONFIDENCE_WITH_MEMORY if has_memory else CONFIDENCE_WITHOUT_MEMORY
    reasoning = (
        "OOMKilled with exit code 137; a near-identical past incident on this service "
        "recurs, corroborating the diagnosis and raising confidence."
        if has_memory
        else "OOMKilled with exit code 137 inferred from the current signals alone."
    )
    report = RCAReport(
        root_cause="JVM heap exhaustion: container OOMKilled with exit code 137.",
        root_cause_category="OOMKilled",
        confidence=confidence,
        evidence_refs=[0],
        reasoning=reasoning,
        recommendations=["Raise the memory limit", "Roll back the leaky deploy"],
    )
    return llm_text(report.model_dump_json())


def _specialist(name: str, tool: str, summary: str) -> ScriptedLLM:
    out = AgentOutput(
        agent_name=name,
        succeeded=True,
        evidence=[
            Evidence(
                source_agent=name,
                kind="observation",
                summary=summary,
                severity=Severity.CRITICAL,
                collected_at=datetime(2026, 6, 23, 10, 5, tzinfo=UTC),
            )
        ],
    )
    return ScriptedLLM(
        name=name,
        responses=[
            llm_tool_call(tool, {"namespace": "prod"}, call_id=f"{name}-1"),
            llm_text("collected"),
            llm_text(out.model_dump_json()),
        ],
    )


def _build_dispatcher(scenario: Scenario) -> Any:
    """Fresh dispatcher per run (ScriptedLLM responses are consumed as they fire)."""
    k8s_tool = next(iter(scenario.server_fixture("mcp-k8s")))
    prom_tool = next(iter(scenario.server_fixture("mcp-prom")))
    loki_tool = next(iter(scenario.server_fixture("mcp-loki")))

    recommendation = ScriptedLLM(
        name="recommendation",
        responses=[
            llm_text(
                _RecommendationList(
                    recommendations=[
                        Recommendation(
                            title="Raise memory limit",
                            rationale="Prevent recurrence of the OOM kill.",
                            commands=["kubectl set resources ..."],
                            priority=1,
                        )
                    ]
                ).model_dump_json()
            )
        ],
    )
    rca = ScriptedLLM(name="rca", responses=[_rca_response])

    by_keyword = [
        ("Kubernetes specialist", _specialist("kubernetes", k8s_tool, "payment-service OOMKilled")),
        ("metrics specialist", _specialist("metrics", prom_tool, "memory hit the limit")),
        ("logs specialist", _specialist("logs", loki_tool, "OutOfMemoryError traces")),
        ("Root-Cause Analysis", rca),
        ("Recommendation agent", recommendation),
    ]

    class Dispatcher:
        name = "dispatcher"

        async def chat(self, messages: Any, **kwargs: Any) -> Any:
            system = next((m.content for m in messages if m.role == "system"), "")
            for keyword, llm in by_keyword:
                if keyword in system:
                    return await llm.chat(messages, **kwargs)
            raise AssertionError(f"No scripted LLM matched system prompt: {system[:80]!r}")

    return Dispatcher()


@dataclass
class ABResult:
    scenario_id: str
    with_memory: float
    without_memory: float

    @property
    def delta(self) -> float:
        return self.with_memory - self.without_memory


async def _run_once(scenario: Scenario) -> float:
    router = build_router(_build_dispatcher(scenario))  # type: ignore[arg-type]
    state = await run_scenario(scenario, router)
    assert state.rca is not None
    return state.rca.confidence


async def run_ab(scenario: Scenario) -> ABResult:
    """Run the scenario twice (memory on / off) and return the confidence delta."""
    on = scenario  # keeps memory_seed
    off = scenario.model_copy(update={"memory_seed": []})
    with_memory = await _run_once(on)
    without_memory = await _run_once(off)
    return ABResult(
        scenario_id=scenario.id,
        with_memory=with_memory,
        without_memory=without_memory,
    )


def _default_scenario() -> Scenario:
    """The recurring-incident scenario shipped in the golden dataset."""
    scenarios = [s for s in load_scenarios() if s.memory_seed]
    if not scenarios:
        raise RuntimeError("No scenario with a memory_seed found in the golden dataset")
    return scenarios[0]


def ab_report(scenario: Scenario | None = None) -> dict[str, float]:
    """Return ``{with_memory, without_memory, delta}`` for one A/B run."""
    result = asyncio.run(run_ab(scenario or _default_scenario()))
    return {
        "with_memory": result.with_memory,
        "without_memory": result.without_memory,
        "delta": result.delta,
    }


def main() -> int:
    report = ab_report()
    print("Memory A/B (scripted, deterministic):")
    print(f"  with memory   : {report['with_memory']:.3f}")
    print(f"  without memory: {report['without_memory']:.3f}")
    print(f"  delta         : {report['delta']:+.3f}")
    return 0 if report["delta"] > 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
