"""Anthropic provider (Claude via langchain-anthropic)."""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderNotConfigured,
    ToolCall,
    ToolSchema,
)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise ProviderNotConfigured("ANTHROPIC_API_KEY is not set")
        self._api_key = api_key

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
        client = ChatAnthropic(
            api_key=self._api_key,  # type: ignore[arg-type]
            model_name=model,
            temperature=temperature,
            max_tokens_to_sample=max_tokens or 4096,
        )

        if tools:
            client = client.bind_tools(
                [
                    {"name": t.name, "description": t.description, "input_schema": t.parameters}
                    for t in tools
                ]
            )

        lc_messages = [_to_lc_message(m) for m in messages]
        raw = await client.ainvoke(lc_messages)

        return _to_response(raw, model=model, provider=self.name, schema=response_schema)


def _to_lc_message(m: Message) -> Any:
    match m.role:
        case "system":
            return SystemMessage(content=m.content)
        case "user":
            return HumanMessage(content=m.content)
        case "assistant":
            if m.tool_calls:
                return AIMessage(
                    content=m.content,
                    tool_calls=[
                        {"name": tc.name, "args": tc.arguments, "id": tc.id, "type": "tool_call"}
                        for tc in m.tool_calls
                    ],
                )
            return AIMessage(content=m.content)
        case "tool":
            return ToolMessage(content=m.content, tool_call_id=m.tool_call_id or "")
        case _:
            raise ValueError(f"Unknown message role: {m.role}")


def _to_response(
    raw: AIMessage,
    *,
    model: str,
    provider: str,
    schema: type[BaseModel] | None,
) -> LLMResponse:
    content = raw.content if isinstance(raw.content, str) else _extract_text(raw.content)

    tool_calls: list[ToolCall] = []
    for tc in getattr(raw, "tool_calls", []) or []:
        tool_calls.append(
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                arguments=tc.get("args", {}) or {},
            )
        )

    # NOTE: structured-output validation is intentionally NOT done here — the
    # caller validates against ``schema`` and owns the fallback (see base.py).
    _ = schema

    usage = getattr(raw, "usage_metadata", None) or {}
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=getattr(raw, "response_metadata", {}).get("stop_reason"),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        model=model,
        provider=provider,
    )


def _extract_text(blocks: list[Any]) -> str:
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(str(b.get("text", "")))
        elif isinstance(b, str):
            parts.append(b)
    return "".join(parts)


__all__ = ["AnthropicProvider"]
