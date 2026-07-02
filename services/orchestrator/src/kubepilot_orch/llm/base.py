"""LLM provider contract.

All providers (Anthropic / OpenAI / Bedrock / Azure / Ollama / vLLM) implement
``LLMProvider``. The router picks a provider+model per role from config.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Role(StrEnum):
    """LLM call role. Lets a cheap model handle routing and a strong model handle RCA."""

    ROUTING = "routing"
    ANALYSIS = "analysis"
    SUMMARIZATION = "summarization"


class Message(BaseModel):
    """A single chat message."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None  # set when role == "tool"
    name: str | None = None  # tool name (when role == "tool")


class ToolSchema(BaseModel):
    """JSON-schema description of a tool the model may call."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class ToolCall(BaseModel):
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Provider-agnostic response from a chat call."""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""


@runtime_checkable
class LLMProvider(Protocol):
    """The contract every concrete provider implements."""

    name: str

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
        """Send a chat request.

        Implementations MUST:
        - return ``LLMResponse.content == ""`` when only tool calls are produced
        - validate ``response_schema`` on the way out when provided
        - populate ``input_tokens`` and ``output_tokens`` for cost accounting
        """
        ...


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot be reached (network, missing key, etc.)."""


class ProviderNotConfigured(RuntimeError):
    """Raised when no API key / endpoint is configured for a provider that's selected."""
