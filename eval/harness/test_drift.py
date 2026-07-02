"""Deterministic self-test for drift detection + the release gate (Phase 3 W8).

No live LLM: exercises the gate logic on synthetic metrics and score breakdowns,
asserting that a seeded regression blocks the gate and a healthy run passes.
"""

from __future__ import annotations

from pathlib import Path

from kubepilot_orch.state import RCAReport

from eval.harness.drift import (
    EvalMetrics,
    compare,
    load_baseline,
    save_baseline,
)
from eval.harness.eval_gate import metrics_from_breakdowns
from eval.harness.loader import load_scenarios
from eval.harness.scorer import score_scenario

_BASELINE = EvalMetrics(mean_score=0.85, category_accuracy=0.90, ece=0.08, n=26)


def test_committed_baseline_loads() -> None:
    baseline = load_baseline()
    assert baseline.n > 0
    assert 0.0 <= baseline.mean_score <= 1.0
    assert 0.0 <= baseline.ece <= 1.0


def test_healthy_run_passes_gate() -> None:
    current = EvalMetrics(mean_score=0.86, category_accuracy=0.92, ece=0.07, n=26)
    report = compare(current, _BASELINE)
    assert not report.drifted
    assert not report.blocks_release


def test_accuracy_regression_blocks_gate() -> None:
    # 12-point accuracy drop (> 5%) must block.
    current = EvalMetrics(mean_score=0.80, category_accuracy=0.78, ece=0.08, n=26)
    report = compare(current, _BASELINE)
    assert report.blocks_release
    assert any("category_accuracy" in r for r in report.regressions)


def test_calibration_regression_blocks_gate() -> None:
    # ECE worsens by 0.10 (> 5%) → drift.
    current = EvalMetrics(mean_score=0.85, category_accuracy=0.90, ece=0.18, n=26)
    report = compare(current, _BASELINE)
    assert report.blocks_release
    assert any("ece" in r for r in report.regressions)


def test_small_wobble_within_threshold_does_not_block() -> None:
    # A 3-point dip is noise, under the 5% slack.
    current = EvalMetrics(mean_score=0.82, category_accuracy=0.87, ece=0.10, n=26)
    report = compare(current, _BASELINE)
    assert not report.blocks_release


def test_baseline_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    save_baseline(_BASELINE, path)
    assert load_baseline(path) == _BASELINE


def test_metrics_from_breakdowns_over_perfect_scenarios() -> None:
    """A perfect run scores 1.0 accuracy and is well-calibrated (low ECE)."""
    scenarios = load_scenarios()
    breakdowns = []
    for s in scenarios:
        exp = s.expected
        rca = RCAReport(
            root_cause="matches",
            root_cause_category=exp.root_cause_category,
            confidence=max(exp.min_confidence, 0.9),
            reasoning="r",
            recommendations=["x"],
        )
        breakdowns.append(score_scenario(s, rca, []))

    metrics = metrics_from_breakdowns(breakdowns)
    assert metrics.n == len(scenarios)
    assert metrics.category_accuracy == 1.0
    # High-confidence + all-correct → the calibration gap is small.
    assert metrics.ece < 0.15
    # A run never blocks when compared against itself as the baseline.
    assert not compare(metrics, metrics).blocks_release
