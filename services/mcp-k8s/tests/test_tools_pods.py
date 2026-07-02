"""Unit tests for list_pods / describe_pod with a mocked k8s client."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mcp_k8s.tools.pods import describe_pod, list_pods


def _container_status(
    name: str,
    *,
    image: str = "img:1",
    ready: bool = True,
    restart_count: int = 0,
    state: str = "running",
    waiting_reason: str | None = None,
    terminated_exit_code: int | None = None,
) -> SimpleNamespace:
    state_obj = SimpleNamespace(running=None, waiting=None, terminated=None)
    if state == "running":
        state_obj.running = SimpleNamespace()
    elif state == "waiting":
        state_obj.waiting = SimpleNamespace(reason=waiting_reason or "Pending")
    elif state == "terminated":
        state_obj.terminated = SimpleNamespace(
            reason="OOMKilled",
            exit_code=terminated_exit_code or 137,
        )
    return SimpleNamespace(
        name=name,
        image=image,
        ready=ready,
        restart_count=restart_count,
        state=state_obj,
        last_state=SimpleNamespace(running=None, waiting=None, terminated=None),
    )


def _fake_pod(
    name: str,
    namespace: str,
    *,
    phase: str,
    containers: list,
    node: str | None = "node-a",
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels={"app": name},
        ),
        spec=SimpleNamespace(
            node_name=node,
            to_dict=lambda: {"nodeName": node, "containers": []},
        ),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=containers,
            pod_ip="10.0.0.1",
            host_ip="192.168.0.1",
            start_time=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
            conditions=[],
            reason=None,
        ),
    )


@pytest.mark.asyncio
async def test_list_pods_running_pod(core_v1: MagicMock) -> None:
    pod = _fake_pod(
        "payment-service",
        "prod",
        phase="Running",
        containers=[_container_status("app", restart_count=0, state="running")],
    )
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    pods = await list_pods("prod")
    assert len(pods) == 1
    p = pods[0]
    assert p.name == "payment-service"
    assert p.phase == "Running"
    assert p.status_reason is None
    assert p.restart_count == 0
    assert p.containers[0].state == "running"


@pytest.mark.asyncio
async def test_list_pods_surfaces_crashloopbackoff(core_v1: MagicMock) -> None:
    pod = _fake_pod(
        "payment-service",
        "prod",
        phase="Running",
        containers=[
            _container_status(
                "app",
                ready=False,
                restart_count=12,
                state="waiting",
                waiting_reason="CrashLoopBackOff",
            )
        ],
    )
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    pods = await list_pods("prod")
    assert pods[0].status_reason == "CrashLoopBackOff"
    assert pods[0].restart_count == 12
    assert pods[0].containers[0].ready is False


@pytest.mark.asyncio
async def test_list_pods_passes_label_selector(core_v1: MagicMock) -> None:
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    await list_pods("prod", label_selector="app=payment-service")
    core_v1.list_namespaced_pod.assert_called_once_with(
        namespace="prod",
        label_selector="app=payment-service",
    )


@pytest.mark.asyncio
async def test_describe_pod_pulls_events(core_v1: MagicMock) -> None:
    pod = _fake_pod(
        "payment-service-0",
        "prod",
        phase="Running",
        containers=[_container_status("app", state="terminated", terminated_exit_code=137)],
    )
    core_v1.read_namespaced_pod.return_value = pod

    event = SimpleNamespace(
        type="Warning",
        reason="BackOff",
        message="Back-off restarting failed container",
        count=5,
        first_timestamp=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
        last_timestamp=datetime(2026, 6, 23, 10, 5, tzinfo=UTC),
        event_time=None,
        involved_object=SimpleNamespace(kind="Pod", name="payment-service-0", namespace="prod"),
        source=SimpleNamespace(component="kubelet"),
    )
    core_v1.list_namespaced_event.return_value = SimpleNamespace(items=[event])

    desc = await describe_pod("prod", "payment-service-0")
    assert desc.containers[0].state == "terminated"
    assert desc.containers[0].exit_code == 137
    assert len(desc.recent_events) == 1
    assert desc.recent_events[0].reason == "BackOff"
    assert desc.recent_events[0].count == 5
