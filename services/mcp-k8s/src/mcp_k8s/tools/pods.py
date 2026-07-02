"""list_pods + describe_pod."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kubernetes import client as k8s_client

from mcp_k8s.client import get_core_v1
from mcp_k8s.models import ContainerStatus, PodDescription, PodSummary
from mcp_k8s.tools.base import Tool, register, to_thread
from mcp_k8s.tools.events import _event_to_model  # internal helper, intentional


async def list_pods(namespace: str, label_selector: str | None = None) -> list[PodSummary]:
    api = get_core_v1()
    raw = await to_thread(
        api.list_namespaced_pod,
        namespace=namespace,
        label_selector=label_selector or "",
    )
    return [_summarize_pod(p) for p in raw.items]


async def describe_pod(namespace: str, name: str) -> PodDescription:
    api = get_core_v1()
    pod = await to_thread(api.read_namespaced_pod, name=name, namespace=namespace)
    summary = _summarize_pod(pod)

    # Pull recent events filtered to this pod.
    fs = f"involvedObject.name={name},involvedObject.namespace={namespace}"
    events_raw = await to_thread(api.list_namespaced_event, namespace=namespace, field_selector=fs)
    events = [_event_to_model(e) for e in events_raw.items]
    events.sort(key=lambda e: e.last_seen or datetime.min, reverse=True)

    spec = pod.spec.to_dict() if pod.spec else {}
    conditions = (
        [c.to_dict() for c in pod.status.conditions or []] if pod.status else []  # type: ignore[union-attr]
    )

    return PodDescription(
        **summary.model_dump(),
        spec=_safe_dict(spec),
        conditions=conditions,
        recent_events=events[:25],
    )


def _summarize_pod(pod: Any) -> PodSummary:
    status = pod.status
    containers: list[ContainerStatus] = []
    restart_total = 0

    for cs in status.container_statuses or []:
        state, reason, exit_code = _container_state(cs.state)
        last_term_reason, last_exit_code = _last_termination(cs.last_state)
        restart_total += cs.restart_count or 0
        containers.append(
            ContainerStatus(
                name=cs.name,
                image=cs.image,
                ready=bool(cs.ready),
                restart_count=int(cs.restart_count or 0),
                state=state,
                state_reason=reason,
                exit_code=exit_code,
                last_termination_reason=last_term_reason,
                last_exit_code=last_exit_code,
            )
        )

    return PodSummary(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        phase=status.phase or "Unknown",
        status_reason=_pod_status_reason(pod),
        node_name=pod.spec.node_name if pod.spec else None,
        pod_ip=status.pod_ip,
        host_ip=status.host_ip,
        start_time=status.start_time,
        restart_count=restart_total,
        containers=containers,
        labels=pod.metadata.labels or {},
    )


def _container_state(
    state: k8s_client.V1ContainerState | None,
) -> tuple[str, str | None, int | None]:
    if state is None:
        return ("unknown", None, None)
    if state.running:
        return ("running", None, None)
    if state.waiting:
        return ("waiting", state.waiting.reason, None)
    if state.terminated:
        return ("terminated", state.terminated.reason, state.terminated.exit_code)
    return ("unknown", None, None)


def _last_termination(
    state: k8s_client.V1ContainerState | None,
) -> tuple[str | None, int | None]:
    if state is None or state.terminated is None:
        return (None, None)
    return (state.terminated.reason, state.terminated.exit_code)


def _pod_status_reason(pod: Any) -> str | None:
    """Derive a human-meaningful status reason — surfaces CrashLoopBackOff etc."""
    for cs in (pod.status.container_statuses or []) if pod.status else []:
        if cs.state and cs.state.waiting and cs.state.waiting.reason:
            reason = cs.state.waiting.reason
            if reason not in {"ContainerCreating", "PodInitializing"}:
                return str(reason)
    return getattr(pod.status, "reason", None) if pod.status else None


def _safe_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Strip None values from a nested dict for compact transport."""
    return {k: v for k, v in d.items() if v is not None}


_LIST_PODS_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string", "description": "Kubernetes namespace"},
        "label_selector": {
            "type": ["string", "null"],
            "description": "Label selector (e.g. 'app=payment-service')",
        },
    },
    "required": ["namespace"],
    "additionalProperties": False,
}

_DESCRIBE_POD_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string"},
        "name": {"type": "string", "description": "Pod name"},
    },
    "required": ["namespace", "name"],
    "additionalProperties": False,
}


register(
    Tool(
        name="list_pods",
        description="List pods in a namespace with status, restart counts, and container state.",
        parameters=_LIST_PODS_SCHEMA,
        handler=list_pods,
    )
)

register(
    Tool(
        name="describe_pod",
        description=(
            "Full pod detail including spec, conditions, container states, and recent events. "
            "Use this to investigate a specific failing pod."
        ),
        parameters=_DESCRIBE_POD_SCHEMA,
        handler=describe_pod,
    )
)
