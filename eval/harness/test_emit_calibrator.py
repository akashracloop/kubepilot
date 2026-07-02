"""Deterministic self-test for the calibrator artifact producer (Phase 3 gap fix).

No live LLM: fits an isotonic calibrator from synthetic score breakdowns, writes
it via run_eval._emit_calibrator, and reloads it through the same from_dict the
gateway uses at startup — proving the produced artifact is loadable.
"""

from __future__ import annotations

import json
from pathlib import Path

from kubepilot_orch.calibration import IsotonicCalibrator

from eval.harness.run_eval import _emit_calibrator
from eval.harness.scorer import ScoreBreakdown


def _bd(confidence: float, correct: bool) -> ScoreBreakdown:
    return ScoreBreakdown(
        scenario_id="s",
        expected_category="OOMKilled",
        actual_category="OOMKilled" if correct else "Other",
        category_match=correct,
        min_confidence=0.7,
        actual_confidence=confidence,
        confidence_ok=confidence >= 0.7,
    )


def test_emit_calibrator_writes_loadable_artifact(tmp_path: Path) -> None:
    # A spread of (confidence, correct) so the isotonic fit has signal.
    breakdowns = [
        _bd(0.9, True),
        _bd(0.85, True),
        _bd(0.8, False),
        _bd(0.6, False),
        _bd(0.5, True),
        _bd(0.4, False),
    ]
    out = tmp_path / "nested" / "calibrator.json"
    _emit_calibrator(breakdowns, out)

    assert out.exists()
    blob = json.loads(out.read_text())
    # Reload exactly as the gateway's _build_calibrator does.
    calibrator = IsotonicCalibrator.from_dict(blob)
    assert calibrator.is_fitted
    # A calibrated confidence is a valid probability.
    value = calibrator.calibrate(0.85)
    assert 0.0 <= value <= 1.0
