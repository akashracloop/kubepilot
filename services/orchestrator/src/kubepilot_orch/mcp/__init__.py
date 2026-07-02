"""Client-side MCP layer (orchestrator's view of MCP servers).

Every agent calls into ``MCPClient`` rather than direct httpx — the client
caches tool descriptors, normalizes errors, and is the single seam where
authentication / tracing / cost accounting will be added in W9.
"""

from kubepilot_orch.mcp.client import MCPClient, MCPError, ToolDescriptor

__all__ = ["MCPClient", "MCPError", "ToolDescriptor"]
