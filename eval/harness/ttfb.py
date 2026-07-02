"""Time-to-first-byte (TTFB) latency eval + gate.

TTFB = trigger → first graph node output — the "is it working?" signal a user
feels before the full RCA lands (the orchestrator logs it per investigation as
``investigation_ttfb``). This harness measures it across the golden scenarios and
gates on the **median** staying under a threshold (default 5s).

Split like the other harness modules: the pure ``summarize_ttfb`` math + gate is
exercised by the deterministic ``test_ttfb.py`` (no LLM); ``measure_ttfb`` is the
live path (needs a key) that streams each scenario and times the first node.
"""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from kubepilot_orch.graph import build_graph
from kubepilot_orch.llm.router import LLMRouter

from eval.harness.loader import Scenario
from eval.harness.runner import build_deps

# The plan's target: the median first-node latency must stay under 5 seconds.
DEFAULT_TTFB_THRESHOLD_S = 5.0


@dataclass(frozen=True)
class TtfbSample:
    scenario_id: str
    ttfb_s: float


@dataclass
class TtfbReport:
    samples: list[TtfbSample] = field(default_factory=list)
    threshold_s: float = DEFAULT_TTFB_THRESHOLD_S

    @property
    def values(self) -> list[float]:
        return [s.ttfb_s for s in self.samples]

    @property
    def median_s(self) -> float:
        return statistics.median(self.values) if self.samples else 0.0

    @property
    def p95_s(self) -> float:
        vals = sorted(self.values)
        if not vals:
            return 0.0
        # Nearest-rank p95.
        idx = min(len(vals) - 1, round(0.95 * (len(vals) - 1)))
        return vals[idx]

    @property
    def max_s(self) -> float:
        return max(self.values) if self.samples else 0.0

    @property
    def passes_gate(self) -> bool:
        # An empty run can't fail the gate (nothing measured).
        return not self.samples or self.median_s < self.threshold_s


def summarize_ttfb(
    samples: list[TtfbSample], *, threshold_s: float = DEFAULT_TTFB_THRESHOLD_S
) -> TtfbReport:
    return TtfbReport(samples=list(samples), threshold_s=threshold_s)


def render_ttfb(report: TtfbReport) -> str:
    status = "PASS" if report.passes_gate else "FAIL"
    return (
        f"TTFB over {len(report.samples)} scenarios — "
        f"median {report.median_s:.2f}s, p95 {report.p95_s:.2f}s, max {report.max_s:.2f}s "
        f"(gate < {report.threshold_s:.0f}s: {status})"
    )


async def measure_ttfb_for_scenario(scenario: Scenario, llm: LLMRouter) -> float:
    """Wall-clock seconds from graph start to its first node output (live)."""
    deps = await build_deps(scenario, llm)
    try:
        graph = build_graph(deps)
        started = time.perf_counter()
        async for mode, _chunk in graph.astream(
            {
                "incident_id": uuid.uuid4(),
                "query": scenario.query,
                "namespace": scenario.namespace,
                "service": scenario.service,
                "started_at": _utcnow(),
            },
            stream_mode=["updates", "values"],
        ):
            if mode == "updates":
                return time.perf_counter() - started
        return time.perf_counter() - started
    finally:
        for client in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki):
            await client.aclose()


async def measure_ttfb(
    scenarios: list[Scenario],
    llm: LLMRouter,
    *,
    threshold_s: float = DEFAULT_TTFB_THRESHOLD_S,
) -> TtfbReport:
    samples = [
        TtfbSample(scenario_id=s.id, ttfb_s=await measure_ttfb_for_scenario(s, llm))
        for s in scenarios
    ]
    return summarize_ttfb(samples, threshold_s=threshold_s)


def _utcnow() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
