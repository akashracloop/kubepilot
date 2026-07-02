"""Release gate — block a regressive release (Phase 3 W8).

    uv run python -m eval.harness.eval_gate                 # gate vs baseline
    uv run python -m eval.harness.eval_gate --update-baseline   # promote current

Runs the live golden eval, computes the gated metrics (mean score, category
accuracy, calibration ECE), and compares them to the committed baseline. Exits
non-zero — **blocking the release** — when any metric regresses beyond its
threshold (default: accuracy/score regression >5%, ECE increase >5%).

This is the LIVE path (needs an API key), wired into ``.github/workflows/eval-gate.yml``
to run on release. The deterministic ``test_drift.py`` / ``test_calibration_eval.py``
self-tests exercise the gate logic without a key so PR CI stays green.
"""

from __future__ import annotations

import asyncio
import sys

from eval.harness.calibration import eval_calibration_report
from eval.harness.drift import EvalMetrics, compare, load_baseline, save_baseline
from eval.harness.loader import Scenario, load_scenarios
from eval.harness.scorer import ScoreBreakdown, aggregate


def metrics_from_breakdowns(breakdowns: list[ScoreBreakdown]) -> EvalMetrics:
    """Collapse per-scenario scores into the gated metrics (score, accuracy, ECE)."""
    agg = aggregate(breakdowns)
    ece = eval_calibration_report(breakdowns).ece
    return EvalMetrics(
        mean_score=agg.mean_score,
        category_accuracy=agg.category_accuracy,
        ece=ece,
        n=agg.count,
    )


def _run_live(scenarios: list[Scenario]) -> list[ScoreBreakdown]:
    # Imported lazily: build_live_router raises without an API key, and we don't
    # want to touch a real provider on import (keeps the self-tests key-free).
    from eval.harness.run_eval import _run_all, build_live_router

    return asyncio.run(_run_all(scenarios, build_live_router()))


def main(argv: list[str] | None = None) -> int:
    from eval.harness.loader import load_heldout

    argv = sys.argv[1:] if argv is None else argv
    update = "--update-baseline" in argv

    current = metrics_from_breakdowns(_run_live(load_scenarios()))

    if update:
        save_baseline(current)
        print(f"Baseline updated: {current.to_dict()}")
        return 0

    baseline = load_baseline()
    report = compare(current, baseline)
    print(report.render())

    # Held-out metrics — tracked separately to surface overfit (not gated unless a
    # held-out baseline is provided; the golden gate is the release blocker).
    heldout = load_heldout()
    if heldout:
        heldout_metrics = metrics_from_breakdowns(_run_live(heldout))
        gap = current.mean_score - heldout_metrics.mean_score
        print(
            f"\nHeld-out: score {heldout_metrics.mean_score:.3f}, "
            f"accuracy {heldout_metrics.category_accuracy:.3f}, ece {heldout_metrics.ece:.3f} "
            f"(golden-held-out gap {gap:+.3f})"
        )

    if report.blocks_release:
        print("\nRELEASE BLOCKED: eval regressed beyond threshold.")
        return 1
    print("\nGate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
