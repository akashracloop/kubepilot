"""get_pipeline_status — latest pipeline/build status."""

from __future__ import annotations

from mcp_ci import client
from mcp_ci.models import PipelineStatus
from mcp_ci.tools.base import Tool, register


async def get_pipeline_status(repo_or_service: str) -> PipelineStatus:
    """Return the latest pipeline/build status for a repo or service.

    Delegates to the configured CI backend (GitHub Actions / Jenkins / ArgoCD).
    """
    backend = client.get_backend()
    return await backend.pipeline_status(repo_or_service)


_SCHEMA = {
    "type": "object",
    "properties": {
        "repo_or_service": {
            "type": "string",
            "description": "Repository or service identifier (slug / job name / app name).",
        },
    },
    "required": ["repo_or_service"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_pipeline_status",
        description=(
            "Return the latest pipeline/build status (succeeded/failed/in_progress) with its "
            "run timestamp. Use this to check whether the delivery pipeline itself is healthy."
        ),
        parameters=_SCHEMA,
        handler=get_pipeline_status,
    )
)
