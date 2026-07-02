"""get_recent_commits — recent commits to a repo."""

from __future__ import annotations

from mcp_ci import client
from mcp_ci.models import CommitList
from mcp_ci.tools.base import Tool, register

_DEFAULT_WINDOW_MINUTES = 60


async def get_recent_commits(
    repo: str, window_minutes: int = _DEFAULT_WINDOW_MINUTES
) -> CommitList:
    """Return recent commits to a repo, for tying an incident back to a code change.

    Delegates to the configured CI backend (GitHub Actions / Jenkins / ArgoCD).
    """
    backend = client.get_backend()
    return await backend.recent_commits(repo, window_minutes)


_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "Repository identifier (owner/name slug / job name / app name).",
        },
        "window_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10080,
            "default": _DEFAULT_WINDOW_MINUTES,
            "description": "Look back this many minutes for commits.",
        },
    },
    "required": ["repo"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_recent_commits",
        description=(
            "List recent commits to a repo with sha, message, author, and timestamp. "
            "Use this to correlate an incident with a specific code change."
        ),
        parameters=_SCHEMA,
        handler=get_recent_commits,
    )
)
