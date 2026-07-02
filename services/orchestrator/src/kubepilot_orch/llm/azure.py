"""Azure OpenAI provider — enterprise-hosted GPT models.

BYOK model: an Azure OpenAI API key + endpoint. ``model`` is the *deployment
name* configured in the Azure resource (which may differ from the base model
name). The API version defaults to a recent GA release and is overridable.
"""

from __future__ import annotations

from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel

from kubepilot_orch.llm._common import openai_tool_dicts, to_lc_messages, to_llm_response
from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderNotConfigured,
    ToolSchema,
)

DEFAULT_API_VERSION = "2024-10-21"


class AzureOpenAIProvider(LLMProvider):
    name = "azure"

    def __init__(
        self,
        api_key: str | None,
        endpoint: str | None,
        api_version: str = DEFAULT_API_VERSION,
    ) -> None:
        if not api_key:
            raise ProviderNotConfigured("AZURE_OPENAI_API_KEY is not set")
        if not endpoint:
            raise ProviderNotConfigured("AZURE_OPENAI_ENDPOINT is not set")
        self._api_key = api_key
        self._endpoint = endpoint
        self._api_version = api_version

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
        client: object = AzureChatOpenAI(
            api_key=self._api_key,  # type: ignore[arg-type]
            azure_endpoint=self._endpoint,
            api_version=self._api_version,
            azure_deployment=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            client = client.bind_tools(openai_tool_dicts(tools))  # type: ignore[attr-defined]

        # response_schema is validated by the caller, not enforced here (see base.py).
        _ = response_schema
        raw = await client.ainvoke(to_lc_messages(messages))  # type: ignore[attr-defined]
        return to_llm_response(raw, model=model, provider=self.name)


__all__ = ["AzureOpenAIProvider"]
