"""Pydantic response models for k8s tools.

Every tool returns a Pydantic model rather than raw dicts. Agents validate
schemas before downstream use (per docs/ARCHITECTURE.md §4 "structured output").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContainerStatus(BaseModel):
    name: str
    image: str
    ready: bool
    restart_count: int
    state: str  # "running" | "waiting" | "terminated"
    state_reason: str | None = None
    exit_code: int | None = None  # populated when terminated
    last_termination_reason: str | None = None
    last_exit_code: int | None = None


class PodSummary(BaseModel):
    name: str
    namespace: str
    phase: str  # Pending | Running | Succeeded | Failed | Unknown
    status_reason: str | None = None  # e.g. CrashLoopBackOff, ImagePullBackOff
    node_name: str | None = None
    pod_ip: str | None = None
    host_ip: str | None = None
    start_time: datetime | None = None
    restart_count: int = 0  # sum across all containers
    containers: list[ContainerStatus] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class PodDescription(PodSummary):
    """Full pod detail — adds spec + conditions + recent events."""

    spec: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    recent_events: list[K8sEvent] = Field(default_factory=list)


class K8sEvent(BaseModel):
    type: str  # Normal | Warning
    reason: str
    message: str
    count: int = 1
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    involved_object_kind: str | None = None
    involved_object_name: str | None = None
    involved_object_namespace: str | None = None
    source_component: str | None = None


# Resolve the forward reference for PodDescription.recent_events
PodDescription.model_rebuild()


class NodeSummary(BaseModel):
    name: str
    ready: bool
    schedulable: bool
    kubelet_version: str | None = None
    os: str | None = None
    architecture: str | None = None
    capacity: dict[str, str] = Field(default_factory=dict)
    allocatable: dict[str, str] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    taints: list[dict[str, Any]] = Field(default_factory=list)


class DeploymentSummary(BaseModel):
    name: str
    namespace: str
    replicas: int = 0
    ready_replicas: int = 0
    available_replicas: int = 0
    updated_replicas: int = 0
    strategy: str | None = None
    image: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None


class ServiceSummary(BaseModel):
    name: str
    namespace: str
    type: str  # ClusterIP | NodePort | LoadBalancer | ExternalName
    cluster_ip: str | None = None
    external_ips: list[str] = Field(default_factory=list)
    ports: list[dict[str, Any]] = Field(default_factory=list)
    selector: dict[str, str] = Field(default_factory=dict)


class PVCSummary(BaseModel):
    name: str
    namespace: str
    status: str  # Pending | Bound | Lost
    storage_class: str | None = None
    requested_storage: str | None = None
    volume_name: str | None = None
    access_modes: list[str] = Field(default_factory=list)


class ConfigMapView(BaseModel):
    name: str
    namespace: str
    keys: list[str] = Field(default_factory=list)
    # Values intentionally NOT included by default to avoid leaking secrets-adjacent
    # data through logs; callers can request a specific key by name in a follow-up call.
