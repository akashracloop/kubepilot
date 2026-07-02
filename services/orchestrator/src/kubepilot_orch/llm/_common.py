"""Shared LangChain <-> provider-agnostic mapping helpers.

Used by the newer providers (Bedrock, Azure) to avoid re-deriving the same
message conversion, tool-call extraction, and usage accounting. The older
providers (Anthropic/OpenAI/Ollama) keep their inline versions; behaviour is
identical.

Structured-output validation is deliberately NOT done here — providers return
the model's raw text and the caller owns validation + fallback (see base.py).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from kubepilot_orch.llm.base import LLMResponse, Message, ToolCall, ToolSchema


def to_lc_messages(messages: list[Message]) -> list[Any]:
    out: list[Any] = []
    for m in messages:
        match m.role:
            case "system":
                out.append(SystemMessage(content=m.content))
            case "user":
                out.append(HumanMessage(content=m.content))
            case "assistant":
                out.append(AIMessage(content=m.content))
            case "tool":
                out.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
            case _:
                raise ValueError(f"Unknown message role: {m.role}")
    return out


def extract_text(content: Any) -> str:
    """Flatten LangChain message content (str or list-of-blocks) into plain text."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def openai_tool_dicts(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Tool schemas in OpenAI function-calling format (LangChain normalizes it)."""
    return [
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


def to_llm_response(raw: AIMessage, *, model: str, provider: str) -> LLMResponse:
    content = extract_text(raw.content)

    tool_calls: list[ToolCall] = []
    for tc in getattr(raw, "tool_calls", []) or []:
        tool_calls.append(
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                arguments=tc.get("args", {}) or {},
            )
        )

    meta = getattr(raw, "response_metadata", {}) or {}
    usage = getattr(raw, "usage_metadata", None) or {}
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=meta.get("stop_reason") or meta.get("finish_reason"),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        model=model,
        provider=provider,
    )
