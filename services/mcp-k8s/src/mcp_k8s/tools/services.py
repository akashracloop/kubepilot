"""get_services — Kubernetes Service resources in a namespace."""

from __future__ import annotations

from typing import Any

from mcp_k8s.client import get_core_v1
from mcp_k8s.models import ServiceSummary
from mcp_k8s.tools.base import Tool, register, to_thread


async def get_services(namespace: str) -> list[ServiceSummary]:
    api = get_core_v1()
    raw = await to_thread(api.list_namespaced_service, namespace=namespace)
    return [_summarize(s) for s in raw.items]


def _summarize(svc: Any) -> ServiceSummary:
    spec = svc.spec
    ports = []
    for p in spec.ports or []:
        ports.append(
            {
                "name": p.name,
                "port": p.port,
                "target_port": str(p.target_port) if p.target_port is not None else None,
                "node_port": p.node_port,
                "protocol": p.protocol,
            }
        )

    return ServiceSummary(
        name=svc.metadata.name,
        namespace=svc.metadata.namespace,
        type=spec.type if spec else "ClusterIP",
        cluster_ip=spec.cluster_ip if spec else None,
        external_ips=list(spec.external_i_ps or []) if spec else [],
        ports=ports,
        selector=spec.selector or {} if spec else {},
    )


_SCHEMA = {
    "type": "object",
    "properties": {"namespace": {"type": "string"}},
    "required": ["namespace"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_services",
        description="List Services in a namespace with type, cluster IP, ports, and selectors.",
        parameters=_SCHEMA,
        handler=get_services,
    )
)
