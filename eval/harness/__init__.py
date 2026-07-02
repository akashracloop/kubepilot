"""Phase-1 golden-RCA evaluation harness.

Public surface:
  - ``load_scenarios`` — read the golden .jsonl into typed models.
  - ``run_scenario`` — drive the full investigation graph against a scenario's
    canned MCP fixture, with any LLM router (live provider or ScriptedLLM).
  - ``score_scenario`` / ``aggregate`` — the §7.2 scoring formula.
  - ``render_report`` — pretty-print the per-scenario table + aggregate baseline.

The *live* accuracy path (real LLM) lives in ``run_eval.py``. The deterministic
harness self-test lives in ``test_harness.py`` and never calls a real model.
"""

from __future__ import annotations

from eval.harness.loader import Expected, Scenario, load_scenarios
from eval.harness.report import render_report
from eval.harness.runner import run_scenario
from eval.harness.scorer import (
    AggregateScore,
    EvidenceHit,
    ScoreBreakdown,
    aggregate,
    score_scenario,
)

__all__ = [
    "AggregateScore",
    "EvidenceHit",
    "Expected",
    "Scenario",
    "ScoreBreakdown",
    "aggregate",
    "load_scenarios",
    "render_report",
    "run_scenario",
    "score_scenario",
]
