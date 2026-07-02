"""Scoring for the golden RCA eval (PHASE_1_PLAN §7.2).

    score = (
        correctly_identified_root_cause    # category matches expected
      + confidence_within_tolerance        # confidence >= min_confidence - tol
      + required_evidence_present          # all must_mention substrings found
    ) / 3

Each of the three components is 0 or 1, so a per-scenario score is one of
{0.0, 0.333, 0.667, 1.0}. The aggregate is the mean across scenarios; the
release gate is aggregate ≥ 0.70.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from kubepilot_orch.state import Evidence, RCAReport

from eval.harness.loader import Scenario

# How far below ``min_confidence`` a score may fall and still count as "within
# tolerance". LLM confidence is noisy; a small band avoids penalising a report
# that is essentially at the target.
DEFAULT_CONFIDENCE_TOLERANCE = 0.05


@dataclass(frozen=True)
class EvidenceHit:
    """Whether one required-evidence substring was found in the RCA text."""

    term: str
    present: bool


@dataclass
class ScoreBreakdown:
    """Per-scenario grade with each sub-component exposed for reporting."""

    scenario_id: str
    expected_category: str
    actual_category: str | None
    category_match: bool
    min_confidence: float
    actual_confidence: float | None
    confidence_ok: bool
    evidence_hits: list[EvidenceHit] = field(default_factory=list)

    @property
    def evidence_ok(self) -> bool:
        """All required-evidence substrings present (vacuously true if none required)."""
        return all(h.present for h in self.evidence_hits)

    @property
    def evidence_hit_count(self) -> int:
        return sum(1 for h in self.evidence_hits if h.present)

    @property
    def score(self) -> float:
        components = (self.category_match, self.confidence_ok, self.evidence_ok)
        return sum(1 for c in components if c) / 3.0


@dataclass
class AggregateScore:
    """Aggregate across all graded scenarios."""

    breakdowns: list[ScoreBreakdown]
    baseline_target: float = 0.70

    @property
    def count(self) -> int:
        return len(self.breakdowns)

    @property
    def mean_score(self) -> float:
        if not self.breakdowns:
            return 0.0
        return sum(b.score for b in self.breakdowns) / len(self.breakdowns)

    @property
    def perfect_count(self) -> int:
        return sum(1 for b in self.breakdowns if b.score == 1.0)

    @property
    def category_accuracy(self) -> float:
        if not self.breakdowns:
            return 0.0
        return sum(1 for b in self.breakdowns if b.category_match) / len(self.breakdowns)

    @property
    def passes_gate(self) -> bool:
        return self.mean_score >= self.baseline_target


def _searchable_text(rca: RCAReport, evidence: list[Evidence]) -> str:
    """Concatenate everything a required-evidence substring may legitimately match."""
    parts: list[str] = [
        rca.root_cause,
        rca.root_cause_category or "",
        rca.reasoning,
        *rca.recommendations,
    ]
    for ev in evidence:
        parts.append(ev.summary)
        parts.append(ev.kind)
        if ev.detail:
            parts.append(json.dumps(ev.detail, default=str))
    return "\n".join(parts).lower()


def score_scenario(
    scenario: Scenario,
    rca: RCAReport | None,
    evidence: list[Evidence],
    *,
    confidence_tolerance: float = DEFAULT_CONFIDENCE_TOLERANCE,
) -> ScoreBreakdown:
    """Grade one scenario's RCA output against its expectations.

    A missing RCA (``rca is None``) fails every component — an investigation that
    produced no report scores 0.
    """
    exp = scenario.expected

    if rca is None:
        return ScoreBreakdown(
            scenario_id=scenario.id,
            expected_category=exp.root_cause_category,
            actual_category=None,
            category_match=False,
            min_confidence=exp.min_confidence,
            actual_confidence=None,
            confidence_ok=False,
            evidence_hits=[EvidenceHit(t, False) for t in exp.must_mention_evidence],
        )

    actual_category = (rca.root_cause_category or "").strip()
    category_match = actual_category.casefold() == exp.root_cause_category.strip().casefold()

    confidence_ok = rca.confidence >= (exp.min_confidence - confidence_tolerance)

    blob = _searchable_text(rca, evidence)
    hits = [EvidenceHit(term, term.casefold() in blob) for term in exp.must_mention_evidence]

    return ScoreBreakdown(
        scenario_id=scenario.id,
        expected_category=exp.root_cause_category,
        actual_category=rca.root_cause_category,
        category_match=category_match,
        min_confidence=exp.min_confidence,
        actual_confidence=rca.confidence,
        confidence_ok=confidence_ok,
        evidence_hits=hits,
    )


def aggregate(breakdowns: list[ScoreBreakdown], *, baseline_target: float = 0.70) -> AggregateScore:
    """Combine per-scenario breakdowns into an aggregate with the release gate."""
    return AggregateScore(breakdowns=list(breakdowns), baseline_target=baseline_target)
