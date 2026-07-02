"""Runtime-specific RCA reasoning libraries (Phase 3 W6).

Per-runtime knowledge (JVM/Node/Python/Go) lives as markdown in this directory,
NOT as branching code. The Logs agent tags evidence with ``detail.runtime`` (see
``mcp-loki`` runtime detection); this module normalizes that tag to a canonical
key and loads the matching library, which the RCA agent injects into its prompt.

Adding a runtime is data-only: drop ``{key}.md`` here and, if the Logs agent emits
a new alias, add it to ``_RUNTIME_ALIASES``. No code path branches on language.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kubepilot_orch.state import InvestigationState

_RUNTIMES_DIR = Path(__file__).resolve().parent

# Map the Logs agent's runtime tag (and common synonyms) → canonical library key.
# Canonical keys match the ``{key}.md`` files shipped in this directory.
_RUNTIME_ALIASES: dict[str, str] = {
    "java": "java",
    "jvm": "java",
    "kotlin": "java",
    "scala": "java",
    "go": "go",
    "golang": "go",
    "python": "python",
    "cpython": "python",
    "py": "python",
    "node": "node",
    "nodejs": "node",
    "node.js": "node",
    "javascript": "node",
    "js": "node",
}


def normalize_runtime(runtime: str | None) -> str | None:
    """Canonicalize a raw runtime tag (case-insensitive). Unknown/none → None."""
    if not runtime:
        return None
    return _RUNTIME_ALIASES.get(runtime.strip().lower())


def detect_runtime(state: InvestigationState) -> str | None:
    """Canonical runtime for the investigation, from evidence ``detail.runtime``.

    Scans evidence in order and returns the first recognized runtime. ``generic``
    and unrecognized tags yield None (no runtime library is injected).
    """
    for ev in state.evidence:
        raw = (ev.detail or {}).get("runtime")
        canonical = normalize_runtime(raw if isinstance(raw, str) else None)
        if canonical is not None:
            return canonical
    return None


@cache
def load_runtime_library(runtime_key: str) -> str | None:
    """Load the markdown library for a canonical runtime key, or None if absent."""
    path = _RUNTIMES_DIR / f"{runtime_key}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def runtime_context(state: InvestigationState) -> tuple[str | None, str | None]:
    """Convenience: ``(canonical_runtime, library_text)`` for the RCA prompt.

    Both are None when no recognized runtime is present, so the RCA degrades to
    its runtime-agnostic behaviour.
    """
    runtime = detect_runtime(state)
    if runtime is None:
        return None, None
    return runtime, load_runtime_library(runtime)


def available_runtimes() -> list[str]:
    """Canonical runtime keys that have a shipped library file (sorted)."""
    return sorted(p.stem for p in _RUNTIMES_DIR.glob("*.md"))
