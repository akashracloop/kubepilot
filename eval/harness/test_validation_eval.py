"""Deterministic self-test for the post-remediation validation eval (W9)."""

from __future__ import annotations

from eval.harness.validation_eval import cases, run


def test_validation_accuracy_meets_gate() -> None:
    r = run()
    assert r["n"] >= 8
    assert r["accuracy"] >= 0.90  # DoD gate


def test_cases_span_all_three_outcomes() -> None:
    kinds = {c.expected for c in cases()}
    assert kinds == {"improved", "regressed", "unchanged"}
