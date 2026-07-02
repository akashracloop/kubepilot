"""get_deployments — deployment status in a namespace."""

from __future__ import annotations

from typing import Any

from mcp_k8s.client import get_apps_v1
from mcp_k8s.models import DeploymentSummary
from mcp_k8s.tools.base import Tool, register, to_thread


async def get_deployments(namespace: str) -> list[DeploymentSummary]:
    api = get_apps_v1()
    raw = await to_thread(api.list_namespaced_deployment, namespace=namespace)
    return [_summarize_deployment(d) for d in raw.items]


def _summarize_deployment(dep: Any) -> DeploymentSummary:
    spec = dep.spec
    status = dep.status

    image = None
    if spec and spec.template and spec.template.spec and spec.template.spec.containers:
        first = spec.template.spec.containers[0]
        image = getattr(first, "image", None)

    return DeploymentSummary(
        name=dep.metadata.name,
        namespace=dep.metadata.namespace,
        replicas=int(spec.replicas or 0) if spec else 0,
        ready_replicas=int(status.ready_replicas or 0) if status else 0,
        available_replicas=int(status.available_replicas or 0) if status else 0,
        updated_replicas=int(status.updated_replicas or 0) if status else 0,
        strategy=spec.strategy.type if spec and spec.strategy else None,
        image=image,
        labels=dep.metadata.labels or {},
        created_at=dep.metadata.creation_timestamp,
    )


_SCHEMA = {
    "type": "object",
    "properties": {"namespace": {"type": "string"}},
    "required": ["namespace"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_deployments",
        description="List deployments in a namespace with replica counts and rollout state.",
        parameters=_SCHEMA,
        handler=get_deployments,
    )
)
