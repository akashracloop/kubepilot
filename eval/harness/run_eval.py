"""Live golden-RCA eval — the accuracy path (needs a real LLM key).

    uv run python -m eval.harness.run_eval          # or: make eval

Runs every scenario in ``eval/datasets/golden_rca_scenarios.jsonl`` through the
full investigation graph against its canned MCP fixture, scores each with the
§7.2 formula, prints the report, and exits non-zero if the aggregate baseline
falls below 70%.

LLM selection (BYOK, in priority order):
  1. ``ANTHROPIC_API_KEY`` → Anthropic  (model: ``KUBEPILOT_EVAL_MODEL`` or
     ``claude-sonnet-4-6``)
  2. ``OPENAI_API_KEY``    → OpenAI     (model: ``KUBEPILOT_EVAL_MODEL`` or ``gpt-4o``)
  3. neither set           → a clear error explaining this is the *live* path.

This module deliberately touches a real provider. The deterministic harness
self-test (``test_harness.py``) never imports the live router.
"""

from __future__ import annotations

import asyncio
import os
import sys

from kubepilot_orch.config import LLMRoleBinding
from kubepilot_orch.llm.base import LLMProvider, Role
from kubepilot_orch.llm.router import LLMRouter

from eval.harness.loader import Scenario, load_scenarios
from eval.harness.report import render_report
from eval.harness.runner import run_scenario
from eval.harness.scorer import ScoreBreakdown, aggregate, score_scenario

_NO_KEY_MESSAGE = (
    "No LLM API key found. `run_eval` is the LIVE accuracy path and requires a "
    "real provider.\n"
    "  - Set ANTHROPIC_API_KEY (uses claude-sonnet-4-6), or\n"
    "  - Set OPENAI_API_KEY (uses gpt-4o).\n"
    "Override the model with KUBEPILOT_EVAL_MODEL.\n"
    "For a key-free check of the harness itself, run:  uv run pytest eval"
)


def build_live_router() -> LLMRouter:
    """Build a single-provider router from whichever API key is present."""
    provider: LLMProvider
    override = os.getenv("KUBEPILOT_EVAL_MODEL")

    if os.getenv("ANTHROPIC_API_KEY"):
        from kubepilot_orch.llm.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
        name, model = "anthropic", override or "claude-sonnet-4-6"
    elif os.getenv("OPENAI_API_KEY"):
        from kubepilot_orch.llm.openai import OpenAIProvider

        provider = OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"])
        name, model = "openai", override or "gpt-4o"
    else:
        raise SystemExit(_NO_KEY_MESSAGE)

    bindings = {role: LLMRoleBinding(provider=name, model=model) for role in Role}
    return LLMRouter(providers={name: provider}, role_bindings=bindings)


async def _run_all(scenarios: list[Scenario], router: LLMRouter) -> list[ScoreBreakdown]:
    breakdowns: list[ScoreBreakdown] = []
    for scenario in scenarios:
        state = await run_scenario(scenario, router)
        breakdowns.append(score_scenario(scenario, state.rca, state.evidence))
    return breakdowns


def main() -> int:
    from eval.harness.loader import load_heldout

    router = build_live_router()

    scenarios = load_scenarios()
    print(f"Running {len(scenarios)} golden RCA scenarios (live LLM)...\n")
    golden = aggregate(asyncio.run(_run_all(scenarios, router)))
    print(render_report(golden))

    # Held-out set — scored separately to detect overfitting to golden (§7).
    heldout_scenarios = load_heldout()
    if heldout_scenarios:
        print(f"\nRunning {len(heldout_scenarios)} HELD-OUT scenarios (overfit check)...\n")
        heldout = aggregate(asyncio.run(_run_all(heldout_scenarios, router)))
        print(render_report(heldout))
        gap = golden.mean_score - heldout.mean_score
        print(f"\nGolden - held-out score gap: {gap:+.3f} (large positive ⇒ overfit risk)")

    return 0 if golden.passes_gate else 1


if __name__ == "__main__":
    sys.exit(main())
