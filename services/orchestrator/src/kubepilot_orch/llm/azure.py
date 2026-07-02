"""Azure OpenAI provider — stub (full implementation in W3-W4)."""

from __future__ import annotations

from pydantic import BaseModel

from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ToolSchema,
)


class AzureOpenAIProvider(LLMProvider):
    name = "azure"

    def __init__(self, api_key: str | None, endpoint: str | None) -> None:
        self._api_key = api_key
        self._endpoint = endpoint

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
        raise NotImplementedError("AzureOpenAIProvider lands in W3-W4")


__all__ = ["AzureOpenAIProvider"]
