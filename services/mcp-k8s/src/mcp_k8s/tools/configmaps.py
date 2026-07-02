"""get_configmap — list keys for a named ConfigMap.

NOTE: by design, this returns the *keys* of the ConfigMap, not the values.
Values may contain credentials-adjacent material; agents that need a specific
value will request it explicitly via a follow-up call (added in W4+ when
the K8s agent's tool allowlist is refined).
"""

from __future__ import annotations

from mcp_k8s.client import get_core_v1
from mcp_k8s.models import ConfigMapView
from mcp_k8s.tools.base import Tool, register, to_thread


async def get_configmap(namespace: str, name: str) -> ConfigMapView:
    api = get_core_v1()
    cm = await to_thread(api.read_namespaced_config_map, name=name, namespace=namespace)
    return ConfigMapView(
        name=cm.metadata.name,
        namespace=cm.metadata.namespace,
        keys=sorted((cm.data or {}).keys()),
    )


_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string"},
        "name": {"type": "string", "description": "ConfigMap name"},
    },
    "required": ["namespace", "name"],
    "additionalProperties": False,
}


register(
    Tool(
        name="get_configmap",
        description=(
            "Return the keys of a ConfigMap (values are intentionally omitted to avoid "
            "leaking credentials-adjacent data through logs)."
        ),
        parameters=_SCHEMA,
        handler=get_configmap,
    )
)
