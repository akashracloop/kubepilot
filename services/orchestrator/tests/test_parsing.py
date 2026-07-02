"""strip_code_fences handles gpt-4o-style ```json fences (a real live-run bug)."""

from __future__ import annotations

from kubepilot_orch.llm.parsing import strip_code_fences


def test_strips_json_fence() -> None:
    fenced = '```json\n{\n  "evidence": []\n}\n```'
    assert strip_code_fences(fenced) == '{\n  "evidence": []\n}'


def test_strips_bare_fence_and_list() -> None:
    assert strip_code_fences("```\n[1, 2]\n```") == "[1, 2]"


def test_passes_through_bare_json() -> None:
    assert strip_code_fences('  {"a": 1}  ') == '{"a": 1}'
