"""Deterministic self-test for the debate-uplift eval (scripted critic, no real model).

Asserts the Phase 3 W3 acceptance: on the held-out set the critiqued RCA is at
least as well-calibrated as single-pass, it improves the ambiguous over-confident
case, it does not regress the clear case, and it escalates exactly the right cases.
"""

from __future__ import annotations

import pytest

from eval.harness.debate_eval import (
    debate_report,
    held_out_cases,
    run_debate,
)


@pytest.mark.asyncio
async def test_critique_improves_calibration_without_regression() -> None:
    results = await run_debate()
    report = debate_report(results)

    # Critique is a net calibration win and never makes a case worse.
    assert report["critiqued_at_least_as_good"] is True
    assert report["no_regression"] is True
    assert report["calibration_uplift"] > 0
    assert report["critiqued_calibration_error"] < report["single_pass_calibration_error"]
    # The critic flags exactly the cases that should escalate.
    assert report["escalation_accuracy"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_ambiguous_case_is_tempered_and_escalated() -> None:
    results = {r.id: r for r in await run_debate()}
    ambiguous = results["ambiguous-overconfident"]
    # Raw RCA was over-confident (0.85); critic tempers it toward the ideal (0.30).
    assert ambiguous.raw_confidence == pytest.approx(0.85)
    assert ambiguous.calibrated_confidence == pytest.approx(0.30)
    assert ambiguous.critiqued_error < ambiguous.single_pass_error
    # Model set escalate=False, but policy forces it on agreement 0.30 (< 0.5).
    assert ambiguous.escalated is True
    assert ambiguous.escalation_correct is True


@pytest.mark.asyncio
async def test_clear_case_is_not_regressed_or_escalated() -> None:
    results = {r.id: r for r in await run_debate()}
    clear = results["clear-oom"]
    assert clear.escalated is False
    assert clear.critiqued_error <= clear.single_pass_error + 1e-9


def test_held_out_set_spans_ambiguous_and_clear() -> None:
    cases = held_out_cases()
    ids = {c.id for c in cases}
    assert {"ambiguous-overconfident", "clear-oom"}.issubset(ids)
    assert any(c.should_escalate for c in cases)
    assert any(not c.should_escalate for c in cases)
