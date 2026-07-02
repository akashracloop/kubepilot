"""Helpers for parsing structured output from LLM text.

Many models (notably gpt-4o / gpt-4o-mini) wrap JSON in a markdown code fence:

    ```json
    { "evidence": [...] }
    ```

``model_validate_json`` then fails because the string doesn't start with ``{``.
``strip_code_fences`` removes the fence so the caller can validate the JSON.
Provider-agnostic — safe to run on already-bare JSON (returns it unchanged).
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(?P<body>.*?)\n?\s*```\s*$",
    re.DOTALL,
)


def strip_code_fences(text: str) -> str:
    """Return the inner content of a ```-fenced block, or the text unchanged."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group("body").strip() if match else stripped
