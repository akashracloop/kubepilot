"""Post-remediation validation eval (Phase 4 W9).

Measures how well ``assess_outcome`` classifies a remediation's effect
(improved / regressed / unchanged) against a labelled set of before/after signal
snapshots. The DoD gate is **≥90% correct** confirm/deny on this set. Pure and
deterministic — no live LLM or cluster.
"""

from __future__ import annotations

from dataclasses import dataclass

from kubepilot_orch.remediation.validation import assess_outcome


@dataclass
class ValidationCase:
    id: str
    before: dict[str, float]
    after: dict[str, float]
    expected: str  # "improved" | "regressed" | "unchanged"


def cases() -> list[ValidationCase]:
    return [
        ValidationCase(
            "oom-fixed",
            {"error_rate": 0.40, "restarts": 12},
            {"error_rate": 0.01, "restarts": 12},
            "improved",
        ),
        ValidationCase(
            "latency-fixed",
            {"error_rate": 0.25, "restarts": 0},
            {"error_rate": 0.02, "restarts": 0},
            "improved",
        ),
        ValidationCase(
            "scale-helped",
            {"error_rate": 0.30, "restarts": 3},
            {"error_rate": 0.05, "restarts": 3},
            "improved",
        ),
        ValidationCase(
            "made-worse",
            {"error_rate": 0.05, "restarts": 2},
            {"error_rate": 0.45, "restarts": 2},
            "regressed",
        ),
        ValidationCase(
            "new-crashes",
            {"error_rate": 0.10, "restarts": 4},
            {"error_rate": 0.10, "restarts": 9},
            "regressed",
        ),
        ValidationCase(
            "no-effect",
            {"error_rate": 0.20, "restarts": 5},
            {"error_rate": 0.19, "restarts": 5},
            "unchanged",
        ),
        ValidationCase(
            "marginal",
            {"error_rate": 0.15, "restarts": 1},
            {"error_rate": 0.13, "restarts": 1},
            "unchanged",
        ),
        ValidationCase(
            "clean-recovery",
            {"error_rate": 0.50, "restarts": 8},
            {"error_rate": 0.00, "restarts": 8},
            "improved",
        ),
        ValidationCase(
            "regression-both",
            {"error_rate": 0.05, "restarts": 1},
            {"error_rate": 0.30, "restarts": 6},
            "regressed",
        ),
        ValidationCase(
            "flat-high",
            {"error_rate": 0.30, "restarts": 2},
            {"error_rate": 0.30, "restarts": 2},
            "unchanged",
        ),
    ]


def run() -> dict[str, float]:
    """Return {n, correct, accuracy} over the labelled validation set."""
    cs = cases()
    correct = sum(1 for c in cs if assess_outcome(c.before, c.after) == c.expected)
    return {"n": len(cs), "correct": correct, "accuracy": correct / len(cs)}


def main() -> int:
    r = run()
    print(
        f"Post-remediation validation: {r['correct']}/{int(r['n'])} correct "
        f"(accuracy {r['accuracy']:.2f}, gate ≥0.90)"
    )
    return 0 if r["accuracy"] >= 0.90 else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
