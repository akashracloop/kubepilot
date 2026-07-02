"""Load agent prompts from the prompts/ directory.

Prompts are version-controlled .md files. They live outside Python so they
can be diffed cleanly, reviewed by non-engineers, and (W11+) hot-reloaded
in development.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Load a prompt by its stem name (e.g. 'kubernetes_agent' → kubernetes_agent.md)."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def reload_prompts() -> None:
    """Clear the cache — useful in dev / tests after editing a .md file."""
    load_prompt.cache_clear()
