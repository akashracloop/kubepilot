"""Confidence calibration (Phase 3 W7).

Learns a monotonic raw-confidence → empirical-accuracy map from eval history
(isotonic regression) and reports the Expected Calibration Error + reliability
curve. The graph stamps ``calibrated_confidence`` from a fitted calibrator.
"""

from __future__ import annotations

from kubepilot_orch.calibration.calibrator import (
    CalibrationReport,
    CalibrationSample,
    IsotonicCalibrator,
    ReliabilityBin,
    calibration_report,
    expected_calibration_error,
    reliability_curve,
)

__all__ = [
    "CalibrationReport",
    "CalibrationSample",
    "IsotonicCalibrator",
    "ReliabilityBin",
    "calibration_report",
    "expected_calibration_error",
    "reliability_curve",
]
