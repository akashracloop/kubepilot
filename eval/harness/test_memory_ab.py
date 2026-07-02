"""Deterministic self-test for the memory A/B (scripted LLM, no real model).

Asserts that seeding long-term memory with a near-identical past incident raises
the scripted RCA's confidence — i.e. the graph really injects retrieved context
into the RCA prompt, and the A/B measures a positive delta.
"""

from __future__ import annotations

import pytest

from eval.harness.loader import load_scenarios
from eval.harness.memory_ab import (
    CONFIDENCE_WITH_MEMORY,
    CONFIDENCE_WITHOUT_MEMORY,
    ab_report,
    run_ab,
)


def _recurring_scenario():
    return next(s for s in load_scenarios() if s.memory_seed)


def test_dataset_has_a_recurring_memory_scenario() -> None:
    scenario = _recurring_scenario()
    assert scenario.memory_seed
    assert scenario.fixture, "recurring scenario still needs specialist fixtures"


@pytest.mark.asyncio
async def test_memory_raises_confidence_delta_positive() -> None:
    scenario = _recurring_scenario()
    result = await run_ab(scenario)
    assert result.with_memory == pytest.approx(CONFIDENCE_WITH_MEMORY)
    assert result.without_memory == pytest.approx(CONFIDENCE_WITHOUT_MEMORY)
    assert result.delta > 0


def test_ab_report_shape_and_positive_delta() -> None:
    report = ab_report()
    assert set(report) == {"with_memory", "without_memory", "delta"}
    assert report["delta"] > 0
    assert report["delta"] == pytest.approx(CONFIDENCE_WITH_MEMORY - CONFIDENCE_WITHOUT_MEMORY)
