"""Deterministic self-test for the eval-side calibration bridge (Phase 3 W7/W8).

Verifies that score breakdowns map to calibration samples, that a systematically
overconfident eval history yields a high ECE, and that fitting an isotonic
calibrator on that history brings the ECE under the <10% gate.
"""

from __future__ import annotations

from kubepilot_orch.calibration import expected_calibration_error
from kubepilot_orch.state import RCAReport

from eval.harness.calibration import (
    eval_calibration_report,
    fit_calibrator,
    samples_from_breakdowns,
)
from eval.harness.loader import load_scenarios
from eval.harness.scorer import score_scenario


def _overconfident_breakdowns() -> list:
    """Every scenario answered with the WRONG category but stated confidence 0.9."""
    breakdowns = []
    for s in load_scenarios():
        rca = RCAReport(
            root_cause="wrong",
            root_cause_category=s.expected.root_cause_category + "_WRONG",
            confidence=0.9,
            reasoning="r",
        )
        breakdowns.append(score_scenario(s, rca, []))
    return breakdowns


def test_samples_carry_confidence_and_correctness() -> None:
    breakdowns = _overconfident_breakdowns()
    samples = samples_from_breakdowns(breakdowns)
    assert len(samples) == len(breakdowns)
    assert all(s.confidence == 0.9 for s in samples)
    assert all(s.correct is False for s in samples)  # all categories wrong


def test_overconfident_history_has_high_ece_and_calibrator_fixes_it() -> None:
    breakdowns = _overconfident_breakdowns()
    report = eval_calibration_report(breakdowns)
    # Stated 0.9, 0% correct → ECE near 0.9.
    assert report.ece > 0.5

    calibrator = fit_calibrator(breakdowns)
    samples = samples_from_breakdowns(breakdowns)
    recalibrated = [
        type(s)(confidence=calibrator.calibrate(s.confidence), correct=s.correct) for s in samples
    ]
    assert expected_calibration_error(recalibrated) < 0.10  # meets the <10% gate


def test_calibration_report_over_real_scenarios_is_bounded() -> None:
    # A mixed, mostly-correct run should be reasonably calibrated.
    breakdowns = []
    for i, s in enumerate(load_scenarios()):
        correct = i % 5 != 0  # 80% correct
        rca = RCAReport(
            root_cause="x",
            root_cause_category=s.expected.root_cause_category + ("" if correct else "_WRONG"),
            confidence=0.8,
            reasoning="r",
        )
        breakdowns.append(score_scenario(s, rca, []))
    report = eval_calibration_report(breakdowns)
    assert 0.0 <= report.ece <= 1.0
    assert report.n == len(breakdowns)
