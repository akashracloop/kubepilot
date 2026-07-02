"""Test-support helpers shipped with the package (a la django.test).

ScriptedLLM lets tests script the exact LLMResponse sequence an agent will see.
``build_mcp_client(handler)`` wraps an httpx.MockTransport so tests can stage
tool-descriptor + tool-result responses without spinning up a real MCP server.

These helpers are public — they're used by KubePilot's own tests and may be
imported by downstream agent-author tests too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel

from kubepilot_orch.config import LLMRoleBinding
from kubepilot_orch.llm.base import LLMResponse, Message, Role, ToolCall, ToolSchema
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.mcp.client import MCPClient


@dataclass
class ScriptedLLM:
    """Fake LLMProvider that returns pre-scripted LLMResponses in order.

    Each entry is either an LLMResponse OR a callable(messages, tools, ...) -> LLMResponse
    so a test can assert on the inputs the agent sent.
    """

    name: str = "scripted"
    responses: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[ToolSchema] | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "response_schema": response_schema,
            }
        )
        if not self.responses:
            raise AssertionError(f"ScriptedLLM({self.name}) ran out of scripted responses")
        item = self.responses.pop(0)
        if callable(item):
            return item(messages=messages, tools=tools, response_schema=response_schema)
        return item


def build_router(
    *providers: ScriptedLLM,
    role_to_provider: dict[Role, str] | None = None,
) -> LLMRouter:
    """Build a router from one or more ScriptedLLMs.

    All Phase 1 roles default to the first provider unless overridden.
    """
    provider_map = {p.name: p for p in providers}
    primary = providers[0].name
    bindings: dict[Role, LLMRoleBinding] = {
        role: LLMRoleBinding(
            provider=(role_to_provider or {}).get(role, primary),
            model="scripted-model",
        )
        for role in (Role.ROUTING, Role.ANALYSIS, Role.SUMMARIZATION)
    }
    return LLMRouter(providers=provider_map, role_bindings=bindings)  # type: ignore[arg-type]


def build_mcp_client(handler, server_name: str = "mcp") -> MCPClient:  # type: ignore[no-untyped-def]
    """Build an MCPClient backed by httpx.MockTransport with the given handler."""
    client = MCPClient(server_name=server_name, base_url=f"http://{server_name}")
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=f"http://{server_name}"
    )
    return client


def llm_text(content: str, *, tokens: int = 20) -> LLMResponse:
    """Shorthand: a no-tool-call LLMResponse with text content."""
    return LLMResponse(
        content=content,
        tool_calls=[],
        input_tokens=tokens,
        output_tokens=tokens,
        model="scripted-pro",
        provider="scripted",
    )


def llm_tool_call(tool: str, arguments: dict[str, Any], *, call_id: str = "call_1") -> LLMResponse:
    """Shorthand: an LLMResponse with a single tool_call."""
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=tool, arguments=arguments)],
        input_tokens=50,
        output_tokens=20,
        model="scripted-pro",
        provider="scripted",
    )


__all__ = [
    "ScriptedLLM",
    "build_mcp_client",
    "build_router",
    "llm_text",
    "llm_tool_call",
]
