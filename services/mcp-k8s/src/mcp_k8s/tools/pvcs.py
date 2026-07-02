"""get_pvcs — PersistentVolumeClaims in a namespace."""

from __future__ import annotations

from typing import Any

from mcp_k8s.client import get_core_v1
from mcp_k8s.models import PVCSummary
from mcp_k8s.tools.base import Tool, register, to_thread


async def get_pvcs(namespace: str) -> list[PVCSummary]:
    api = get_core_v1()
    raw = await to_thread(api.list_namespaced_persistent_volume_claim, namespace=namespace)
    return [_summarize(pvc) for pvc in raw.items]


def _summarize(pvc: Any) -> PVCSummary:
    spec = pvc.spec
    status = pvc.status

    requested = None
    if spec and spec.resources and spec.resources.requests:
        requested = str(spec.resources.requests.get("storage", "")) or None

    return PVCSummary(
        name=pvc.metadata.name,
        namespace=pvc.metadata.namespace,
        status=status.phase if status else "Unknown",
        storage_class=spec.storage_class_name if spec else None,
        requested_storage=requested,
        volume_name=spec.volume_name if spec else None,
        access_modes=list(spec.access_modes or []) if spec else [],
    )


_SCHEMA = {
    "type": "object",
    "properties": {"namespace": {"type": "string"}},
    "required": ["namespace"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_pvcs",
        description="List PersistentVolumeClaims in a namespace with bound/pending status.",
        parameters=_SCHEMA,
        handler=get_pvcs,
    )
)
