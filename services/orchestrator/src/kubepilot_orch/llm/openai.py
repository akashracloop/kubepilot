"""OpenAI provider (GPT-4o family via langchain-openai)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderNotConfigured,
    ToolCall,
    ToolSchema,
)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str | None, base_url: str | None = None) -> None:
        if not api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is not set")
        self._api_key = api_key
        self._base_url = base_url

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
        client = ChatOpenAI(
            api_key=self._api_key,  # type: ignore[arg-type]
            base_url=self._base_url,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            client = client.bind_tools(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    }
                    for t in tools
                ]
            )

        lc_messages = [_to_lc(m) for m in messages]
        raw = await client.ainvoke(lc_messages)

        content = raw.content if isinstance(raw.content, str) else ""
        tool_calls: list[ToolCall] = []
        for tc in getattr(raw, "tool_calls", []) or []:
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", ""),
                    arguments=tc.get("args", {}) or {},
                )
            )

        # Structured-output validation is the caller's responsibility (see base.py);
        # validating here would make the caller-side fallbacks unreachable.
        _ = response_schema

        usage = getattr(raw, "usage_metadata", None) or {}
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=getattr(raw, "response_metadata", {}).get("finish_reason"),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            model=model,
            provider=self.name,
        )


def _to_lc(m: Message) -> Any:
    match m.role:
        case "system":
            return SystemMessage(content=m.content)
        case "user":
            return HumanMessage(content=m.content)
        case "assistant":
            return AIMessage(content=m.content)
        case "tool":
            return ToolMessage(content=m.content, tool_call_id=m.tool_call_id or "")
        case _:
            raise ValueError(f"Unknown message role: {m.role}")


__all__ = ["OpenAIProvider"]
