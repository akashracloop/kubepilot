"""AWS Bedrock provider (Claude / Llama / etc. via the Bedrock Converse API).

BYOK model: credentials come from the standard AWS provider chain (env vars,
shared config, or an IRSA / instance role in-cluster). Only the region is
configured explicitly. ``model`` is a Bedrock model id, e.g.
``anthropic.claude-3-5-sonnet-20241022-v2:0`` or a cross-region inference
profile id.
"""

from __future__ import annotations

from langchain_aws import ChatBedrockConverse
from pydantic import BaseModel

from kubepilot_orch.llm._common import openai_tool_dicts, to_lc_messages, to_llm_response
from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderNotConfigured,
    ToolSchema,
)


class BedrockProvider(LLMProvider):
    name = "bedrock"

    def __init__(self, region: str | None = None) -> None:
        if not region:
            raise ProviderNotConfigured("bedrock_region is not set")
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
        client: object = ChatBedrockConverse(
            model=model,
            region_name=self._region,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            client = client.bind_tools(openai_tool_dicts(tools))  # type: ignore[attr-defined]

        # response_schema is validated by the caller, not enforced here (see base.py).
        _ = response_schema
        raw = await client.ainvoke(to_lc_messages(messages))  # type: ignore[attr-defined]
        return to_llm_response(raw, model=model, provider=self.name)


__all__ = ["BedrockProvider"]
