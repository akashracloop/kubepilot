"""Phase-1 golden-RCA evaluation harness.

Public surface:
  - ``load_scenarios`` — read the golden .jsonl into typed models.
  - ``run_scenario`` — drive the full investigation graph against a scenario's
    canned MCP fixture, with any LLM router (live provider or ScriptedLLM).
  - ``score_scenario`` / ``aggregate`` — the §7.2 scoring formula.
  - ``render_report`` — pretty-print the per-scenario table + aggregate baseline.

The *live* accuracy path (real LLM) lives in ``run_eval.py``. The deterministic
harness self-test lives in ``test_harness.py`` and never calls a real model.

Phase 2 additions (all deterministic, no LLM):
  - ``run_timeline_eval`` / ``score_timeline`` — grade ``build_timeline`` ordering
    + labels (``timeline_eval``).
  - ``ab_report`` / ``run_ab`` — memory-on vs memory-off A/B (``memory_ab``).
"""

from __future__ import annotations

from eval.harness.loader import Expected, MemorySeed, Scenario, load_scenarios
from eval.harness.memory_ab import ABResult, ab_report, run_ab
from eval.harness.report import render_report
from eval.harness.runner import run_scenario
from eval.harness.scorer import (
    AggregateScore,
    EvidenceHit,
    ScoreBreakdown,
    aggregate,
    score_scenario,
)
from eval.harness.timeline_eval import (
    TimelineAggregate,
    TimelineScenario,
    TimelineScore,
    run_timeline_eval,
    score_timeline,
)

__all__ = [
    "ABResult",
    "AggregateScore",
    "EvidenceHit",
    "Expected",
    "MemorySeed",
    "Scenario",
    "ScoreBreakdown",
    "TimelineAggregate",
    "TimelineScenario",
    "TimelineScore",
    "ab_report",
    "aggregate",
    "load_scenarios",
    "render_report",
    "run_ab",
    "run_scenario",
    "run_timeline_eval",
    "score_scenario",
    "score_timeline",
]
