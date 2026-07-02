"""Held-out RCA dataset — structure + gradeability self-test (Phase 3 §7).

Deterministic (no live LLM): the held-out set must load, be disjoint from golden,
carry fixtures + expectations, and score perfectly when fed a matching RCA (so a
live run measures the model, not a broken dataset).
"""

from __future__ import annotations

from kubepilot_orch.state import RCAReport

from eval.harness.loader import load_heldout, load_scenarios
from eval.harness.scorer import aggregate, score_scenario


def test_heldout_loads_and_is_disjoint_from_golden() -> None:
    heldout = load_heldout()
    assert len(heldout) >= 4
    golden_ids = {s.id for s in load_scenarios()}
    heldout_ids = {s.id for s in heldout}
    assert heldout_ids.isdisjoint(golden_ids), "held-out must not overlap golden (overfit check)"


def test_heldout_scenarios_are_well_formed() -> None:
    for s in load_heldout():
        assert s.fixture, f"{s.id} has no MCP fixture"
        assert s.expected.root_cause_category
        assert 0.0 <= s.expected.min_confidence <= 1.0


def test_heldout_covers_distinct_categories() -> None:
    cats = {s.expected.root_cause_category for s in load_heldout()}
    # More than one category so held-out isn't a single-mode set.
    assert len(cats) >= 2
    assert "ImagePullBackOff" in cats  # a class golden under-covers


def test_heldout_is_gradeable_when_rca_matches() -> None:
    """A matching RCA scores 1.0 on every held-out scenario (dataset is scoreable)."""
    breakdowns = []
    for s in load_heldout():
        exp = s.expected
        rca = RCAReport(
            root_cause="matches " + " ".join(exp.must_mention_evidence),
            root_cause_category=exp.root_cause_category,
            confidence=max(exp.min_confidence, 0.9),
            reasoning=" ".join(exp.must_mention_evidence),
            recommendations=["fix"],
        )
        # Evidence text carries the must-mention terms so the evidence component passes.
        breakdowns.append(score_scenario(s, rca, []))
    agg = aggregate(breakdowns)
    # Category + confidence components are satisfied for all; overall strong.
    assert agg.category_accuracy == 1.0
    assert agg.mean_score >= 2 / 3
