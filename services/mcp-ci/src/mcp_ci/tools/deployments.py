"""get_deployment_history — recent deploys of a service."""

from __future__ import annotations

from mcp_ci import client
from mcp_ci.models import DeploymentHistory
from mcp_ci.tools.base import Tool, register

_DEFAULT_WINDOW_MINUTES = 60


async def get_deployment_history(
    service: str, window_minutes: int = _DEFAULT_WINDOW_MINUTES
) -> DeploymentHistory:
    """Return recent deployments of a service, most useful for correlating an
    incident window against a deploy that landed shortly before it.

    Delegates to the configured CI backend (GitHub Actions / Jenkins / ArgoCD).
    """
    backend = client.get_backend()
    return await backend.deployment_history(service, window_minutes)


_SCHEMA = {
    "type": "object",
    "properties": {
        "service": {
            "type": "string",
            "description": "Service identifier (repo slug / job name / app name per backend).",
        },
        "window_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10080,
            "default": _DEFAULT_WINDOW_MINUTES,
            "description": "Look back this many minutes for deployments.",
        },
    },
    "required": ["service"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_deployment_history",
        description=(
            "List recent deployments of a service with version, timestamp, and status. "
            "Use this to check whether a deploy landed just before an incident window."
        ),
        parameters=_SCHEMA,
        handler=get_deployment_history,
    )
)
