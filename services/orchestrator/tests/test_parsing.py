"""LLM-output cleanup: strip_code_fences / strip_json_comments / clean_json.

strip_code_fences handles gpt-4o-style ```json fences (a real live-run bug); the
comment stripping handles a second live-run bug where gpt-4o-mini decorated the
JSON with a `//` inline comment (caught by the Phase 4 minikube e2e).
"""

from __future__ import annotations

import json

from kubepilot_orch.llm.parsing import clean_json, strip_code_fences, strip_json_comments


def test_strips_json_fence() -> None:
    fenced = '```json\n{\n  "evidence": []\n}\n```'
    assert strip_code_fences(fenced) == '{\n  "evidence": []\n}'


def test_strips_bare_fence_and_list() -> None:
    assert strip_code_fences("```\n[1, 2]\n```") == "[1, 2]"


def test_passes_through_bare_json() -> None:
    assert strip_code_fences('  {"a": 1}  ') == '{"a": 1}'


def test_strip_line_comment_outside_string() -> None:
    raw = '{\n  "image": "app:tag"  // replace with real tag\n}'
    assert json.loads(strip_json_comments(raw)) == {"image": "app:tag"}


def test_strip_block_comment() -> None:
    raw = '{"a": 1, /* note */ "b": 2}'
    assert json.loads(strip_json_comments(raw)) == {"a": 1, "b": 2}


def test_preserves_double_slash_inside_strings() -> None:
    # A URL value must survive — the // is inside a string literal.
    raw = '{"url": "http://svc:8080/mcp"}'
    assert json.loads(strip_json_comments(raw)) == {"url": "http://svc:8080/mcp"}


def test_preserves_escaped_quote_then_comment() -> None:
    raw = '{"q": "a \\" b"} // trailing'
    assert json.loads(strip_json_comments(raw)) == {"q": 'a " b'}


def test_clean_json_composes_fence_and_comments() -> None:
    raw = '```json\n{\n  "image": "x:1"  // bad\n}\n```'
    assert json.loads(clean_json(raw)) == {"image": "x:1"}


def test_clean_json_noop_on_bare_json() -> None:
    assert clean_json('{"a": 1}') == '{"a": 1}'
