"""Capability-based MCP routing (Phase 2).

Agents declare a *capability domain* — ``kubernetes`` / ``metrics`` / ``logs`` /
``tracing`` / ``deployment`` — instead of a concrete MCP server. The router maps
each domain to whichever ``MCPClient`` provides it. This is the seam that lets an
operator remap a domain to a *different* MCP server via config without touching
agent code — e.g. point ``metrics`` + ``logs`` + ``tracing`` at the single
official **Grafana MCP** server (one server, three signals), or swap in a
community / vendor MCP.

KubePilot's own servers remain the default reference implementation; the adapter
just stops the orchestrator from being hard-wired to them. See
docs/ARCHITECTURE.md §3.3.1 and docs/mcp-adapters.md.
"""

from __future__ import annotations

from enum import StrEnum

from kubepilot_orch.mcp.client import MCPClient


class Capability(StrEnum):
    """Signal domains an agent can request. One MCP server may back several."""

    KUBERNETES = "kubernetes"
    METRICS = "metrics"
    LOGS = "logs"
    TRACING = "tracing"
    DEPLOYMENT = "deployment"


class UnknownCapabilityError(KeyError):
    """Raised when a capability has no configured MCP server."""

    def __init__(self, capability: str, available: list[str]) -> None:
        self.capability = capability
        self.available = available
        super().__init__(
            f"No MCP server configured for capability {capability!r}; "
            f"available: {available}"
        )


class CapabilityRouter:
    """Resolves a capability domain to the MCP client that serves it."""

    def __init__(self, routes: dict[str, MCPClient]) -> None:
        # Keyed by the capability's string value so both Capability and raw str work.
        self._routes = {str(k): v for k, v in routes.items()}

    def client(self, capability: str | Capability) -> MCPClient:
        key = str(capability)
        try:
            return self._routes[key]
        except KeyError:
            raise UnknownCapabilityError(key, self.capabilities()) from None

    def has(self, capability: str | Capability) -> bool:
        return str(capability) in self._routes

    def capabilities(self) -> list[str]:
        return sorted(self._routes)

    async def aclose(self) -> None:
        """Close each underlying client once (clients may be shared across domains)."""
        seen: dict[int, MCPClient] = {}
        for client in self._routes.values():
            seen[id(client)] = client
        for client in seen.values():
            await client.aclose()


def build_default_router(
    *,
    kubernetes: MCPClient,
    metrics: MCPClient,
    logs: MCPClient,
    tracing: MCPClient | None = None,
    deployment: MCPClient | None = None,
) -> CapabilityRouter:
    """Wire the KubePilot reference servers. Tracing/deployment are Phase 2 optional."""
    routes: dict[str, MCPClient] = {
        Capability.KUBERNETES: kubernetes,
        Capability.METRICS: metrics,
        Capability.LOGS: logs,
    }
    if tracing is not None:
        routes[Capability.TRACING] = tracing
    if deployment is not None:
        routes[Capability.DEPLOYMENT] = deployment
    return CapabilityRouter(routes)


def build_router_from_endpoints(endpoints: dict[str, str]) -> CapabilityRouter:
    """Build a router from a ``{capability: base_url}`` map (config-driven).

    Endpoints sharing a URL share ONE ``MCPClient`` instance — so mapping
    ``metrics`` / ``logs`` / ``tracing`` to the same Grafana MCP URL yields a
    single client (one connection pool, one server), which is exactly the
    "official Grafana MCP" swap the adapter exists to enable.
    """
    by_url: dict[str, MCPClient] = {}
    routes: dict[str, MCPClient] = {}
    for capability, url in endpoints.items():
        normalized = url.rstrip("/")
        client = by_url.get(normalized)
        if client is None:
            client = MCPClient(server_name=str(capability), base_url=normalized)
            by_url[normalized] = client
        routes[str(capability)] = client
    return CapabilityRouter(routes)
