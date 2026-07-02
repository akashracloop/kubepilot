"""Deterministic self-test for prompt A/B + rollback (Phase 3 W9).

No live LLM: exercises the promotion decision (a worse or noisy prompt is
rejected, a clearly-better one is promoted, an accuracy regression is rejected)
and the rollback lever (pinning ``active`` to an older version).
"""

from __future__ import annotations

from pathlib import Path

from kubepilot_orch.agents.prompt_registry import PromptRegistry

from eval.harness.prompt_ab import ArmResult, decide_promotion, pinned
from eval.harness.scorer import EvidenceHit, ScoreBreakdown


def _bd(cat: bool, conf: bool, ev: bool) -> ScoreBreakdown:
    return ScoreBreakdown(
        scenario_id="s",
        expected_category="OOMKilled",
        actual_category="OOMKilled" if cat else "Other",
        category_match=cat,
        min_confidence=0.8,
        actual_confidence=0.85,
        confidence_ok=conf,
        evidence_hits=[EvidenceHit("t", ev)],
    )


def _arm(version: str, specs: list[tuple[bool, bool, bool]]) -> ArmResult:
    return ArmResult(version=version, breakdowns=[_bd(*s) for s in specs])


def test_clearly_better_prompt_is_promoted() -> None:
    current = _arm("v1", [(True, True, False)] * 10)  # score 0.667 each
    challenger = _arm("v2", [(True, True, True)] * 10)  # score 1.0 each
    decision = decide_promotion("rca_agent", current, challenger)
    assert decision.promote is True
    assert decision.gain > decision.margin


def test_worse_prompt_is_rejected() -> None:
    current = _arm("v1", [(True, True, True)] * 10)  # 1.0
    challenger = _arm("v2", [(True, False, False)] * 10)  # 0.333
    decision = decide_promotion("rca_agent", current, challenger)
    assert decision.promote is False
    assert "rejected" in decision.reason


def test_within_noise_margin_is_rejected() -> None:
    same = [(True, True, False)] * 10
    decision = decide_promotion("rca_agent", _arm("v1", same), _arm("v2", same))
    assert decision.promote is False
    assert decision.gain == 0.0
    assert "noise" in decision.reason


def test_accuracy_regression_is_rejected_even_if_score_rises() -> None:
    # Challenger scores higher overall but gets categories wrong → reject.
    current = _arm("v1", [(True, False, False)] * 10)  # score 0.333, accuracy 1.0
    challenger = _arm("v2", [(False, True, True)] * 10)  # score 0.667, accuracy 0.0
    decision = decide_promotion("rca_agent", current, challenger)
    assert decision.challenger_score > decision.current_score
    assert decision.promote is False
    assert "category_accuracy regressed" in decision.reason


def test_rollback_via_active_pin(tmp_path: Path) -> None:
    (tmp_path / "rca_agent.md").write_text("v1 prompt", encoding="utf-8")
    (tmp_path / "rca_agent.v2.md").write_text("v2 prompt", encoding="utf-8")
    reg = PromptRegistry(prompts_dir=tmp_path)

    # Default active is the latest (v2).
    assert reg.active_version("rca_agent") == "v2"
    assert reg.render("rca_agent") == "v2 prompt"

    # Rollback: pin active back to v1 (the config-flip lever, no code change).
    with pinned(reg, "rca_agent", "v1"):
        assert reg.active_version("rca_agent") == "v1"
        assert reg.render("rca_agent") == "v1 prompt"

    # Pin lifted → back to latest.
    assert reg.active_version("rca_agent") == "v2"
