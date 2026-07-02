"""Deterministic self-test for the timeline eval (no LLM).

Asserts the scorer gives ~1.0 for a correctly-ordered timeline scenario and
strictly <1.0 when the expected order is shuffled or a required label is missing.
"""

from __future__ import annotations

import pytest

from eval.harness.timeline_eval import (
    TimelineExpected,
    load_timeline_scenarios,
    run_scenario_timeline,
    run_timeline_eval,
    score_timeline,
)


def test_dataset_loads_and_has_scenarios() -> None:
    scenarios = load_timeline_scenarios()
    assert len(scenarios) >= 3
    for s in scenarios:
        assert s.expected.ordered_labels, f"{s.id} declares no ordered_labels"


def test_correct_scenarios_score_one_and_pass_gate() -> None:
    agg = run_timeline_eval()
    for s in agg.scores:
        assert s.score == pytest.approx(1.0), f"{s.scenario_id} produced {s.produced_labels}"
    assert agg.passes_gate
    assert agg.mean_score == pytest.approx(1.0)


def test_shuffled_expected_order_scores_below_one() -> None:
    scenario = next(s for s in load_timeline_scenarios() if len(s.expected.ordered_labels) >= 2)
    baseline = run_scenario_timeline(scenario)
    assert baseline.score == pytest.approx(1.0)

    # Reverse the expected order → the real (timestamp-ordered) timeline no longer
    # matches, so the ordering component collapses and the score drops below 1.0.
    shuffled = scenario.model_copy(
        update={
            "expected": TimelineExpected(
                ordered_labels=list(reversed(scenario.expected.ordered_labels)),
                must_include_labels=scenario.expected.must_include_labels,
            )
        }
    )
    state = shuffled.build_state()
    from kubepilot_orch.timeline import build_timeline

    entries = build_timeline(state, finished_at=shuffled.finished_at)
    shuffled_score = score_timeline(shuffled, entries)
    assert shuffled_score.score < 1.0
    assert shuffled_score.order_score < 1.0


def test_missing_required_label_scores_below_one() -> None:
    scenario = next(iter(load_timeline_scenarios()))
    mismatched = scenario.model_copy(
        update={
            "expected": TimelineExpected(
                ordered_labels=scenario.expected.ordered_labels,
                must_include_labels=[
                    *scenario.expected.must_include_labels,
                    "label_never_produced",
                ],
            )
        }
    )
    result = run_scenario_timeline(mismatched)
    assert result.inclusion_score < 1.0
    assert result.score < 1.0
