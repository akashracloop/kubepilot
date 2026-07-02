"""Deterministic timeline eval — grades ``kubepilot_orch.timeline.build_timeline``.

No LLM. Each scenario stages a set of ``Evidence`` (with timestamps), a minimal
RCA, and a ``finished_at`` bookend, then declares the timeline labels we expect.
``build_timeline`` orders events purely from timestamps + a stable kind→label map,
so the whole path is reproducible.

Scoring (per scenario), each component in [0, 1], averaged:
  - ordering  — of the expected ``ordered_labels``, the fraction of adjacent pairs
    that appear in the correct RELATIVE order in the produced timeline (a missing
    label breaks its pairs).
  - inclusion — the fraction of ``must_include_labels`` present in the timeline.

Aggregate = mean per-scenario score; the release gate is ≥ 0.85.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from kubepilot_orch.state import Evidence, InvestigationState, RCAReport, TimelineEntry
from kubepilot_orch.timeline import build_timeline
from pydantic import BaseModel, Field

from eval.harness.loader import _load_jsonl  # reuse the strict jsonl reader

DEFAULT_TIMELINE_DATASET = (
    Path(__file__).resolve().parent.parent / "datasets" / "golden_timeline_scenarios.jsonl"
)

TIMELINE_GATE = 0.85


class TimelineExpected(BaseModel):
    """What the produced timeline must look like."""

    ordered_labels: list[str] = Field(default_factory=list)
    must_include_labels: list[str] = Field(default_factory=list)


class _RcaStub(BaseModel):
    root_cause: str
    root_cause_category: str | None = None


class TimelineScenario(BaseModel):
    """One hand-authored timeline scenario."""

    id: str
    namespace: str = "prod"
    service: str | None = None
    started_at: datetime
    finished_at: datetime
    evidence: list[Evidence] = Field(default_factory=list)
    rca: _RcaStub
    expected: TimelineExpected

    def build_state(self) -> InvestigationState:
        """Materialise the ``InvestigationState`` that ``build_timeline`` reads."""
        return InvestigationState(
            incident_id=uuid.uuid4(),
            query=f"timeline scenario {self.id}",
            namespace=self.namespace,
            service=self.service,
            started_at=self.started_at,
            evidence=list(self.evidence),
            rca=RCAReport(
                root_cause=self.rca.root_cause,
                root_cause_category=self.rca.root_cause_category,
                confidence=0.9,
                reasoning="timeline scenario stub",
            ),
        )


def load_timeline_scenarios(path: str | Path | None = None) -> list[TimelineScenario]:
    dataset = Path(path) if path is not None else DEFAULT_TIMELINE_DATASET
    return [TimelineScenario.model_validate(blob) for blob in _load_jsonl(dataset)]


def _order_score(expected: list[str], produced: list[str]) -> float:
    """Fraction of adjacent expected-label pairs that keep their relative order.

    A label absent from ``produced`` breaks every pair it participates in. With
    0 or 1 expected labels: 1.0 iff all expected labels are present.
    """
    positions = [produced.index(lbl) if lbl in produced else -1 for lbl in expected]
    if len(expected) <= 1:
        return 1.0 if all(p >= 0 for p in positions) else 0.0
    good = sum(1 for a, b in pairwise(positions) if 0 <= a < b)
    return good / (len(expected) - 1)


def _inclusion_score(must_include: list[str], produced: list[str]) -> float:
    if not must_include:
        return 1.0
    present = sum(1 for lbl in must_include if lbl in produced)
    return present / len(must_include)


@dataclass
class TimelineScore:
    """Per-scenario timeline grade."""

    scenario_id: str
    produced_labels: list[str]
    order_score: float
    inclusion_score: float

    @property
    def score(self) -> float:
        return (self.order_score + self.inclusion_score) / 2.0


def score_timeline(scenario: TimelineScenario, entries: list[TimelineEntry]) -> TimelineScore:
    produced = [e.label for e in entries]
    return TimelineScore(
        scenario_id=scenario.id,
        produced_labels=produced,
        order_score=_order_score(scenario.expected.ordered_labels, produced),
        inclusion_score=_inclusion_score(scenario.expected.must_include_labels, produced),
    )


def run_scenario_timeline(scenario: TimelineScenario) -> TimelineScore:
    """Build + grade one scenario's timeline (deterministic, no LLM)."""
    state = scenario.build_state()
    entries = build_timeline(state, finished_at=scenario.finished_at)
    return score_timeline(scenario, entries)


@dataclass
class TimelineAggregate:
    scores: list[TimelineScore]
    gate: float = TIMELINE_GATE

    @property
    def mean_score(self) -> float:
        return sum(s.score for s in self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def passes_gate(self) -> bool:
        return self.mean_score >= self.gate


def run_timeline_eval(path: str | Path | None = None) -> TimelineAggregate:
    scenarios = load_timeline_scenarios(path)
    return TimelineAggregate(scores=[run_scenario_timeline(s) for s in scenarios])


def _render(agg: TimelineAggregate) -> str:
    lines = ["scenario                              order  incl   score", "-" * 52]
    for s in agg.scores:
        lines.append(
            f"{s.scenario_id:<36}  {s.order_score:.2f}  {s.inclusion_score:.2f}  {s.score:.2f}"
        )
    gate = "PASS" if agg.passes_gate else "FAIL"
    lines.append("-" * 52)
    lines.append(f"aggregate {agg.mean_score:.3f}  (gate >= {agg.gate:.2f})  {gate}")
    return "\n".join(lines)


def main() -> int:
    agg = run_timeline_eval()
    print(_render(agg))
    return 0 if agg.passes_gate else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
