"""Calibration metrics over eval results (Phase 3 W7/W8).

Bridges the golden-eval score breakdowns to the orchestrator's calibration lib:
each scenario contributes one ``CalibrationSample`` — the RCA's stated confidence
and whether the category was correct. From those we compute the Expected
Calibration Error (the <10% gate) + a reliability curve for the AgentOps plot, and
can fit an ``IsotonicCalibrator`` to ship.
"""

from __future__ import annotations

from kubepilot_orch.calibration import (
    CalibrationReport,
    CalibrationSample,
    IsotonicCalibrator,
    calibration_report,
)

from eval.harness.scorer import ScoreBreakdown


def samples_from_breakdowns(breakdowns: list[ScoreBreakdown]) -> list[CalibrationSample]:
    """One (confidence, correct) sample per scored scenario that produced a confidence."""
    return [
        CalibrationSample(confidence=b.actual_confidence, correct=b.category_match)
        for b in breakdowns
        if b.actual_confidence is not None
    ]


def eval_calibration_report(
    breakdowns: list[ScoreBreakdown], *, n_bins: int = 10
) -> CalibrationReport:
    return calibration_report(samples_from_breakdowns(breakdowns), n_bins=n_bins)


def fit_calibrator(breakdowns: list[ScoreBreakdown]) -> IsotonicCalibrator:
    """Fit an isotonic calibrator from eval history (ship its ``to_dict()``)."""
    return IsotonicCalibrator().fit(samples_from_breakdowns(breakdowns))
