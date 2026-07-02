"""Tool descriptor + dispatch registry (mirrors mcp-k8s/tools/base.py).

Kept per-service rather than extracted to a shared library — the duplication
cost is ~40 LOC x 4 servers, while a shared package adds versioning overhead.
If a 5th MCP server lands, revisit and extract.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for arguments
    handler: Callable[..., Awaitable[BaseModel | list[BaseModel]]]

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class _Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def list_descriptors(self) -> list[dict[str, Any]]:
        return [t.descriptor() for t in sorted(self.tools.values(), key=lambda t: t.name)]


REGISTRY = _Registry()


def register(tool: Tool) -> Tool:
    REGISTRY.register(tool)
    return tool
