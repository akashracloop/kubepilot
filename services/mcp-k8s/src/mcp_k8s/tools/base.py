"""Tool descriptor + dispatch registry.

Each tool is a callable with:
  - a JSON Schema describing its arguments (returned by /mcp/tools)
  - an async handler that returns a Pydantic model

The registry is populated at import time by side-effect of importing each
tool module.
"""

from __future__ import annotations

import asyncio
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


def to_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Awaitable[Any]:
    """Sugar for awaiting a sync function in a thread (the k8s client is sync)."""
    return asyncio.to_thread(fn, *args, **kwargs)
