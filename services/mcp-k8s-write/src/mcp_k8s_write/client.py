"""Kubernetes API client loader for the write server.

Mirrors mcp-k8s's loader (in-cluster when running as a pod, else the local
kubeconfig for dev). The write ServiceAccount is bound to a least-privilege
ClusterRole generated from ``safety.required_rbac()`` — so even with a real
client this server can only perform the curated write verbs.
"""

from __future__ import annotations

import os
from functools import lru_cache

import structlog
from kubernetes import client, config

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _load_config() -> None:
    """Load kube config exactly once (env override → in-cluster → default)."""
    explicit = os.getenv("KUBEPILOT_KUBECONFIG")
    if explicit:
        config.load_kube_config(config_file=explicit)
        log.info("kube_config_loaded", source="env", path=explicit)
        return
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
        log.info("kube_config_loaded", source="in_cluster")
        return
    config.load_kube_config()
    log.info("kube_config_loaded", source="default_kubeconfig")


def get_core_v1() -> client.CoreV1Api:
    _load_config()
    return client.CoreV1Api()


def get_apps_v1() -> client.AppsV1Api:
    _load_config()
    return client.AppsV1Api()


def reset_config_cache() -> None:
    """For tests — clears the cached config load so the next call reloads."""
    _load_config.cache_clear()
