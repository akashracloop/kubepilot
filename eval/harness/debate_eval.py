"""Debate uplift — does the critic agent improve on a single-pass RCA?

Phase 3 W3. The critic runs *after* RCA and produces an independent agreement
score, a critic-adjusted confidence, and an escalate-to-human flag. This eval
measures whether that critique is a net improvement on a held-out set of labelled
cases, along two axes the plan calls out (§7):

  - **Calibration uplift** — on cases where the RCA's stated confidence is wrong
    (over-confident on ambiguous evidence), the critic-adjusted confidence should
    land closer to the case's ideal confidence. We report the mean absolute
    calibration error single-pass (raw RCA confidence) vs critiqued (calibrated).
  - **No regression on clear cases** — on well-supported cases the critic must NOT
    manufacture doubt or wrongly escalate.
  - **Escalation accuracy** — the critic flags exactly the cases that should go to
    a human.

The LLM is a ``ScriptedLLM`` (fully deterministic) in the self-test: each case's
critique is scripted, and the *policy* (escalation thresholds, derived confidence)
is exercised by the real ``critic_agent.run``. The live path (``make eval``) swaps
in a real router to measure genuine uplift — ScriptedLLM bypasses provider
message conversion, so the live run is the one that validates the prompt.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kubepilot_orch.agents import critic_agent
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.state import (
    AgentOutput,
    Critique,
    Evidence,
    InvestigationState,
    RCAReport,
    Severity,
)
from kubepilot_orch.testing import ScriptedLLM, build_router, llm_text


@dataclass
class DebateCase:
    """One held-out labelled case: an RCA under a known-ideal confidence + verdict."""

    id: str
    evidence: list[Evidence]
    rca: RCAReport
    scripted_critique: Critique  # what a well-behaved critic returns for this case
    ideal_confidence: float  # the confidence the finding *should* have carried
    should_escalate: bool  # whether a human should review it


@dataclass
class CaseResult:
    id: str
    raw_confidence: float
    calibrated_confidence: float
    ideal_confidence: float
    escalated: bool
    should_escalate: bool

    @property
    def single_pass_error(self) -> float:
        return abs(self.raw_confidence - self.ideal_confidence)

    @property
    def critiqued_error(self) -> float:
        return abs(self.calibrated_confidence - self.ideal_confidence)

    @property
    def escalation_correct(self) -> bool:
        return self.escalated == self.should_escalate


def _ev(agent: str, kind: str, summary: str, severity: Severity = Severity.WARNING) -> Evidence:
    return Evidence(
        source_agent=agent,
        kind=kind,
        summary=summary,
        severity=severity,
        collected_at=datetime(2026, 7, 2, 10, 8, tzinfo=UTC),
    )


def held_out_cases() -> list[DebateCase]:
    """A small held-out debate set spanning ambiguous, clear, and moderate cases."""
    return [
        # 1. Over-confident RCA on a single weak signal — critic should temper + escalate.
        DebateCase(
            id="ambiguous-overconfident",
            evidence=[_ev("logs", "log_pattern", "one connection timeout log line")],
            rca=RCAReport(
                root_cause="Network partition between payment-service and its database.",
                root_cause_category="NetworkPartition",
                confidence=0.85,
                evidence_refs=[0],
                reasoning="A timeout log line suggests the DB was unreachable.",
                recommendations=["Check network policies"],
            ),
            scripted_critique=Critique(
                agreement=0.3,
                concerns=[
                    "A single timeout log line does not establish a partition.",
                    "No metrics/k8s corroboration; alternative causes (slow query, GC pause) "
                    "not ruled out.",
                ],
                adjusted_confidence=0.3,
                escalate_to_human=False,  # policy must still force escalation
            ),
            ideal_confidence=0.3,
            should_escalate=True,
        ),
        # 2. Well-corroborated OOM — critic must NOT regress it.
        DebateCase(
            id="clear-oom",
            evidence=[
                _ev("kubernetes", "pod_state", "OOMKilled, exit 137", Severity.CRITICAL),
                _ev("metrics", "saturation", "memory 256Mi→1Gi in 15m", Severity.CRITICAL),
                _ev("logs", "exception", "java.lang.OutOfMemoryError x23", Severity.CRITICAL),
            ],
            rca=RCAReport(
                root_cause="JVM heap exhaustion; OOMKilled corroborated across three specialists.",
                root_cause_category="OOMKilled",
                confidence=0.92,
                evidence_refs=[0, 1, 2],
                reasoning="K8s OOMKilled + memory saturation + OOM traces all agree.",
                recommendations=["Raise memory limit", "Roll back leaky deploy"],
            ),
            scripted_critique=Critique(
                agreement=0.95,
                concerns=[],
                adjusted_confidence=0.9,
                escalate_to_human=False,
            ),
            ideal_confidence=0.9,
            should_escalate=False,
        ),
        # 3. Moderate confidence, minor gap — critic trims slightly, no escalation.
        DebateCase(
            id="moderate-deploy-correlation",
            evidence=[
                _ev("deployment", "recent_deploy", "v2.3.1 deployed 8m before latency spike"),
                _ev("tracing", "latency", "p99 up 3x on checkout-service"),
            ],
            rca=RCAReport(
                root_cause="Deploy v2.3.1 introduced a latency regression.",
                root_cause_category="DeploymentRegression",
                confidence=0.70,
                evidence_refs=[0, 1],
                reasoning="Deploy correlates in time with the latency spike.",
                recommendations=["Roll back to v2.3.0"],
            ),
            scripted_critique=Critique(
                agreement=0.75,
                concerns=["Correlation is temporal; a concurrent config change wasn't ruled out."],
                adjusted_confidence=0.65,
                escalate_to_human=False,
            ),
            ideal_confidence=0.65,
            should_escalate=False,
        ),
    ]


def _state_for(case: DebateCase) -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.uuid4(),
        query="why is the service unhealthy?",
        namespace="prod",
        service="payment-service",
        evidence=case.evidence,
        agent_outputs={"rca": AgentOutput(agent_name="rca", succeeded=True)},
        completed_agents=["rca"],
        rca=case.rca,
        confidence=case.rca.confidence,
        started_at=datetime(2026, 7, 2, 10, 7, tzinfo=UTC),
    )


async def run_case(case: DebateCase, llm: LLMRouter) -> CaseResult:
    """Run the real critic over one case and record calibration + escalation."""
    critique = await critic_agent.run(_state_for(case), llm=llm)
    calibrated = (
        critique.adjusted_confidence
        if critique.adjusted_confidence is not None
        else case.rca.confidence
    )
    return CaseResult(
        id=case.id,
        raw_confidence=case.rca.confidence,
        calibrated_confidence=calibrated,
        ideal_confidence=case.ideal_confidence,
        escalated=critique.escalate_to_human,
        should_escalate=case.should_escalate,
    )


def _scripted_router(cases: list[DebateCase]) -> LLMRouter:
    """A ScriptedLLM returning each case's critique in order (cases run sequentially)."""
    scripted = ScriptedLLM(
        name="critic",
        responses=[llm_text(c.scripted_critique.model_dump_json()) for c in cases],
    )
    return build_router(scripted)


async def run_debate(
    cases: list[DebateCase] | None = None, llm: LLMRouter | None = None
) -> list[CaseResult]:
    """Run every case through the critic. Scripted (deterministic) unless a router is given."""
    cases = cases or held_out_cases()
    router = llm or _scripted_router(cases)
    return [await run_case(c, router) for c in cases]


def debate_report(results: list[CaseResult]) -> dict[str, Any]:
    """Aggregate calibration uplift + escalation accuracy across the held-out set."""
    n = len(results)
    single_pass_err = sum(r.single_pass_error for r in results) / n
    critiqued_err = sum(r.critiqued_error for r in results) / n
    escalation_acc = sum(1 for r in results if r.escalation_correct) / n
    # Critique must never make a clear case worse: no case's calibration error grows.
    no_regression = all(r.critiqued_error <= r.single_pass_error + 1e-9 for r in results)
    return {
        "n": n,
        "single_pass_calibration_error": round(single_pass_err, 4),
        "critiqued_calibration_error": round(critiqued_err, 4),
        "calibration_uplift": round(single_pass_err - critiqued_err, 4),
        "escalation_accuracy": round(escalation_acc, 4),
        "no_regression": no_regression,
        "critiqued_at_least_as_good": critiqued_err <= single_pass_err + 1e-9,
    }


def main() -> int:
    results = asyncio.run(run_debate())
    report = debate_report(results)
    print("Debate uplift (scripted, deterministic):")
    print(f"  cases                       : {report['n']}")
    print(f"  single-pass calibration err : {report['single_pass_calibration_error']:.3f}")
    print(f"  critiqued calibration err   : {report['critiqued_calibration_error']:.3f}")
    print(f"  calibration uplift          : {report['calibration_uplift']:+.3f}")
    print(f"  escalation accuracy         : {report['escalation_accuracy']:.3f}")
    print(f"  no regression on any case   : {report['no_regression']}")
    ok = report["critiqued_at_least_as_good"] and report["no_regression"]
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
