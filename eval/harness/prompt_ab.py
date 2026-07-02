"""Prompt A/B + promotion gate (Phase 3 W9).

A prompt change ships as a new version (``{name}.vN.md``). Before it becomes the
active version it must **beat the current version on the eval set** — this module
runs the golden scenarios under each pinned prompt version and decides promotion:
the challenger is promoted only if it improves mean score beyond a noise margin
*and* does not regress category accuracy. A worse prompt is rejected; rollback is
a one-line ``active`` pin flip (see ``PromptRegistry.active``), no code change.

LLM non-determinism is the enemy of A/B (temperature=0 helps, a margin handles the
rest). The deterministic self-test drives the decision logic without a live model;
the live A/B (needs a key) measures real prompts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from kubepilot_orch.agents.prompt_registry import PromptRegistry, default_registry
from kubepilot_orch.llm.router import LLMRouter

from eval.harness.loader import Scenario
from eval.harness.runner import run_scenario
from eval.harness.scorer import ScoreBreakdown, aggregate, score_scenario

# Minimum mean-score gain required to promote — a guard against LLM noise.
DEFAULT_PROMOTION_MARGIN = 0.02


@dataclass
class ArmResult:
    """One A/B arm: the prompt version tested and its scored breakdowns."""

    version: str
    breakdowns: list[ScoreBreakdown]

    @property
    def mean_score(self) -> float:
        return aggregate(self.breakdowns).mean_score

    @property
    def category_accuracy(self) -> float:
        return aggregate(self.breakdowns).category_accuracy


@dataclass
class PromotionDecision:
    prompt_name: str
    current: str  # current active version
    challenger: str  # proposed version
    current_score: float
    challenger_score: float
    margin: float
    promote: bool
    reason: str

    @property
    def gain(self) -> float:
        return self.challenger_score - self.current_score


def decide_promotion(
    prompt_name: str,
    current: ArmResult,
    challenger: ArmResult,
    *,
    margin: float = DEFAULT_PROMOTION_MARGIN,
) -> PromotionDecision:
    """Promote the challenger only if it clears the margin without regressing accuracy."""
    gain = challenger.mean_score - current.mean_score
    accuracy_regressed = challenger.category_accuracy < current.category_accuracy - 1e-9

    if accuracy_regressed:
        promote, reason = (
            False,
            (
                f"rejected: category_accuracy regressed "
                f"{current.category_accuracy:.3f} → {challenger.category_accuracy:.3f}"
            ),
        )
    elif gain > margin:
        promote, reason = True, f"promoted: mean_score +{gain:.3f} (> margin {margin:.3f})"
    else:
        promote, reason = (
            False,
            (f"rejected: mean_score gain {gain:+.3f} within noise margin {margin:.3f}"),
        )

    return PromotionDecision(
        prompt_name=prompt_name,
        current=current.version,
        challenger=challenger.version,
        current_score=current.mean_score,
        challenger_score=challenger.mean_score,
        margin=margin,
        promote=promote,
        reason=reason,
    )


@contextmanager
def pinned(registry: PromptRegistry, name: str, version: str) -> Iterator[None]:
    """Temporarily pin ``name`` to ``version`` on ``registry`` (restores on exit)."""
    had = name in registry.active
    prev = registry.active.get(name)
    registry.active[name] = version
    try:
        yield
    finally:
        if had:
            registry.active[name] = prev  # type: ignore[assignment]
        else:
            registry.active.pop(name, None)


async def score_arm(
    scenarios: list[Scenario],
    router: LLMRouter,
    *,
    prompt_name: str,
    version: str,
    registry: PromptRegistry | None = None,
) -> ArmResult:
    """Run + score every scenario with ``prompt_name`` pinned to ``version``."""
    reg = registry or default_registry()
    breakdowns: list[ScoreBreakdown] = []
    with pinned(reg, prompt_name, version):
        for scenario in scenarios:
            state = await run_scenario(scenario, router)
            breakdowns.append(score_scenario(scenario, state.rca, state.evidence))
    return ArmResult(version=version, breakdowns=breakdowns)


async def run_prompt_ab(
    scenarios: list[Scenario],
    router: LLMRouter,
    *,
    prompt_name: str,
    current_version: str,
    challenger_version: str,
    margin: float = DEFAULT_PROMOTION_MARGIN,
    registry: PromptRegistry | None = None,
) -> PromotionDecision:
    """A/B two prompt versions over the golden set and decide promotion."""
    current = await score_arm(
        scenarios, router, prompt_name=prompt_name, version=current_version, registry=registry
    )
    challenger = await score_arm(
        scenarios, router, prompt_name=prompt_name, version=challenger_version, registry=registry
    )
    return decide_promotion(prompt_name, current, challenger, margin=margin)
