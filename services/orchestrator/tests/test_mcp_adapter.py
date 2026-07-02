"""Tests for the capability-based MCP router (Phase 2 adapter pattern)."""

from __future__ import annotations

import pytest
from kubepilot_orch.mcp.adapter import (
    Capability,
    CapabilityRouter,
    UnknownCapabilityError,
    build_default_router,
    build_router_from_endpoints,
)
from kubepilot_orch.mcp.client import MCPClient


def _client(name: str, url: str = "http://x") -> MCPClient:
    return MCPClient(server_name=name, base_url=url)


def test_default_router_resolves_core_domains() -> None:
    k8s, prom, loki = _client("mcp-k8s"), _client("mcp-prom"), _client("mcp-loki")
    router = build_default_router(kubernetes=k8s, metrics=prom, logs=loki)

    assert router.client(Capability.KUBERNETES) is k8s
    assert router.client("metrics") is prom  # raw string works too
    assert router.client(Capability.LOGS) is loki
    assert router.capabilities() == ["kubernetes", "logs", "metrics"]
    assert not router.has(Capability.TRACING)


def test_default_router_includes_optional_p2_domains() -> None:
    router = build_default_router(
        kubernetes=_client("k"),
        metrics=_client("m"),
        logs=_client("l"),
        tracing=_client("tempo"),
        deployment=_client("ci"),
    )
    assert router.has(Capability.TRACING)
    assert router.has(Capability.DEPLOYMENT)
    assert router.client("tracing").server_name == "tempo"


def test_unknown_capability_raises_with_available_list() -> None:
    router = build_default_router(kubernetes=_client("k"), metrics=_client("m"), logs=_client("l"))
    with pytest.raises(UnknownCapabilityError) as exc:
        router.client("tracing")
    assert exc.value.capability == "tracing"
    assert "kubernetes" in exc.value.available


def test_endpoints_router_shares_client_for_same_url() -> None:
    # The Grafana-MCP swap: metrics + logs + tracing all point at one server.
    grafana = "http://grafana-mcp.kubepilot-system:8080"
    router = build_router_from_endpoints(
        {
            "kubernetes": "http://mcp-k8s.kubepilot-system:8080",
            "metrics": grafana,
            "logs": grafana + "/",  # trailing slash normalized to the same client
            "tracing": grafana,
        }
    )
    m = router.client("metrics")
    assert router.client("logs") is m  # one shared MCPClient
    assert router.client("tracing") is m
    assert router.client("kubernetes") is not m  # distinct server


async def test_router_aclose_closes_each_client_once() -> None:
    shared = _client("grafana")
    k8s = _client("mcp-k8s")
    router = CapabilityRouter(
        {"metrics": shared, "logs": shared, "tracing": shared, "kubernetes": k8s}
    )
    # Should not raise despite `shared` being referenced by three domains.
    await router.aclose()
