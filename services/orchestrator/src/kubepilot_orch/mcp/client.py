"""HTTP client for KubePilot's MCP servers.

Each MCP server (mcp-k8s, mcp-prom, mcp-loki, ...) exposes:
  GET  /mcp/tools     → tool descriptors with JSON Schema
  POST /mcp/invoke    → { tool, arguments } → { tool, result }
  GET  /mcp/health    → liveness

Tool descriptors are cached on first call (servers don't change their tool
surface at runtime in Phase 1). Pass ``refresh=True`` to bypass the cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)


class MCPError(RuntimeError):
    """Raised when an MCP server returns a non-2xx for /mcp/invoke."""

    def __init__(self, server: str, tool: str, status: int, detail: Any) -> None:
        self.server = server
        self.tool = tool
        self.status = status
        self.detail = detail
        super().__init__(f"{server}.{tool}: HTTP {status} — {detail!r}")


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for arguments


class MCPClient:
    """HTTP client for one MCP server.

    Typical setup wires one client per MCP service:
        k8s = MCPClient("mcp-k8s", "http://mcp-k8s.kubepilot-system:8080")
        prom = MCPClient("mcp-prom", "http://mcp-prom.kubepilot-system:8080")
    """

    def __init__(self, server_name: str, base_url: str, timeout: float = 30.0) -> None:
        self.server_name = server_name
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._tools_cache: list[ToolDescriptor] | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def health(self) -> dict[str, Any]:
        resp = await self._http.get("/mcp/health")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def list_tools(self, *, refresh: bool = False) -> list[ToolDescriptor]:
        if self._tools_cache is None or refresh:
            resp = await self._http.get("/mcp/tools")
            resp.raise_for_status()
            data = resp.json()
            self._tools_cache = [
                ToolDescriptor(
                    name=t["name"],
                    description=t["description"],
                    parameters=t.get("parameters", {}),
                )
                for t in data.get("tools", [])
            ]
        return list(self._tools_cache)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def invoke(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        payload = {"tool": tool, "arguments": arguments or {}}
        resp = await self._http.post("/mcp/invoke", json=payload)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except (ValueError, KeyError):
                detail = resp.text
            log.error(
                "mcp_invoke_failed",
                server=self.server_name,
                tool=tool,
                status=resp.status_code,
            )
            raise MCPError(self.server_name, tool, resp.status_code, detail)

        body = resp.json()
        return body.get("result")
