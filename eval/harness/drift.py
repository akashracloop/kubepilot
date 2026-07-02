"""Drift detection + release gate (Phase 3 W8).

Compares an eval run's metrics against a stored baseline (the last tagged
release). A **drift alert** fires when accuracy, mean score, or calibration
degrades beyond the allowed slack; the release gate **blocks** on the same
condition. Thresholds default to the plan's rule: an accuracy regression >5%
blocks a release.

Metrics are plain JSON so a baseline can be committed and updated at tag time
(``eval_gate.py --update-baseline``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default committed baseline location.
DEFAULT_BASELINE_PATH = Path(__file__).resolve().parent.parent / "baselines" / "golden.json"


@dataclass(frozen=True)
class EvalMetrics:
    """The metrics a release is gated on."""

    mean_score: float
    category_accuracy: float
    ece: float
    n: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_score": round(self.mean_score, 4),
            "category_accuracy": round(self.category_accuracy, 4),
            "ece": round(self.ece, 4),
            "n": self.n,
        }

    @classmethod
    def from_dict(cls, blob: dict[str, Any]) -> EvalMetrics:
        return cls(
            mean_score=float(blob["mean_score"]),
            category_accuracy=float(blob["category_accuracy"]),
            ece=float(blob["ece"]),
            n=int(blob.get("n", 0)),
        )


@dataclass(frozen=True)
class DriftThresholds:
    """How far a metric may move against us before it counts as drift."""

    max_score_regression: float = 0.05
    max_accuracy_regression: float = 0.05
    max_ece_increase: float = 0.05


@dataclass
class DriftReport:
    baseline: EvalMetrics
    current: EvalMetrics
    thresholds: DriftThresholds
    regressions: list[str] = field(default_factory=list)

    @property
    def drifted(self) -> bool:
        return bool(self.regressions)

    @property
    def blocks_release(self) -> bool:
        """The gate blocks on any regression beyond threshold."""
        return self.drifted

    def render(self) -> str:
        lines = [
            "Eval drift vs baseline:",
            f"  mean_score       : {self.current.mean_score:.3f} "
            f"(baseline {self.baseline.mean_score:.3f})",
            f"  category_accuracy: {self.current.category_accuracy:.3f} "
            f"(baseline {self.baseline.category_accuracy:.3f})",
            f"  ece              : {self.current.ece:.3f} (baseline {self.baseline.ece:.3f})",
        ]
        if self.regressions:
            lines.append("  REGRESSIONS:")
            lines.extend(f"    - {r}" for r in self.regressions)
        else:
            lines.append("  no regression beyond threshold")
        return "\n".join(lines)


def compare(
    current: EvalMetrics,
    baseline: EvalMetrics,
    thresholds: DriftThresholds | None = None,
) -> DriftReport:
    """Flag every metric that regressed past its threshold vs the baseline."""
    thresholds = thresholds or DriftThresholds()
    regressions: list[str] = []

    score_drop = baseline.mean_score - current.mean_score
    if score_drop > thresholds.max_score_regression:
        regressions.append(
            f"mean_score dropped {score_drop:.3f} (> {thresholds.max_score_regression:.3f}): "
            f"{baseline.mean_score:.3f} → {current.mean_score:.3f}"
        )

    acc_drop = baseline.category_accuracy - current.category_accuracy
    if acc_drop > thresholds.max_accuracy_regression:
        regressions.append(
            f"category_accuracy dropped {acc_drop:.3f} (> {thresholds.max_accuracy_regression:.3f}): "
            f"{baseline.category_accuracy:.3f} → {current.category_accuracy:.3f}"
        )

    ece_rise = current.ece - baseline.ece
    if ece_rise > thresholds.max_ece_increase:
        regressions.append(
            f"ece worsened {ece_rise:.3f} (> {thresholds.max_ece_increase:.3f}): "
            f"{baseline.ece:.3f} → {current.ece:.3f}"
        )

    return DriftReport(
        baseline=baseline, current=current, thresholds=thresholds, regressions=regressions
    )


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> EvalMetrics:
    return EvalMetrics.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_baseline(metrics: EvalMetrics, path: Path = DEFAULT_BASELINE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics.to_dict(), indent=2) + "\n", encoding="utf-8")
