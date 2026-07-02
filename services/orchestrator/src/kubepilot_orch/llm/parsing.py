"""Helpers for parsing structured output from LLM text.

Many models (notably gpt-4o / gpt-4o-mini) wrap JSON in a markdown code fence:

    ```json
    { "evidence": [...] }
    ```

``model_validate_json`` then fails because the string doesn't start with ``{``.
``strip_code_fences`` removes the fence so the caller can validate the JSON.
Provider-agnostic — safe to run on already-bare JSON (returns it unchanged).

Models also sometimes decorate JSON with JavaScript-style comments, e.g.
gpt-4o-mini emitting ``"image": "x:tag"  // replace with the real tag`` — which
is not valid JSON and makes ``model_validate_json`` fail. ``strip_json_comments``
removes ``//`` line and ``/* */`` block comments while respecting string literals
(so a URL like ``http://host`` inside a value is left intact). ``clean_json``
composes both — the recommended pre-validation cleanup for model output.
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


def strip_json_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments outside string literals.

    A single-pass scanner tracks whether we're inside a double-quoted string
    (honouring backslash escapes), so ``//`` and ``/*`` inside a value — most
    importantly URLs like ``"http://svc"`` — are preserved. Safe on comment-free
    JSON (returns it unchanged).
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:  # keep the escaped char verbatim
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":  # line comment → to EOL
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":  # block comment → past */
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def clean_json(text: str) -> str:
    """Strip code fences then JSON comments — the standard model-output cleanup."""
    return strip_json_comments(strip_code_fences(text))
