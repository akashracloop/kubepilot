"""AWS Bedrock provider — stub (full implementation in W3-W4).

Will use ``langchain-aws.ChatBedrock`` with Anthropic or Llama models.
"""

from __future__ import annotations

from pydantic import BaseModel

from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ToolSchema,
)


class BedrockProvider(LLMProvider):
    name = "bedrock"

    def __init__(self, region: str | None = None) -> None:
        self._region = region

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
        raise NotImplementedError("BedrockProvider lands in W3-W4")


__all__ = ["BedrockProvider"]
