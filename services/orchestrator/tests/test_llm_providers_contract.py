"""Contract tests for all six LLM providers.

Each provider wraps a LangChain chat model. We can't call the real cloud/local
backends in CI, so we monkeypatch the underlying LangChain class with a fake and
assert every provider maps a LangChain ``AIMessage`` into our provider-agnostic
``LLMResponse`` identically — text content, tool calls, and token usage.

This is the "LLM abstraction passes a contract test against all providers" item
from PHASE_1_PLAN.md §5.1. Real-backend calls live behind the ``live_llm`` marker.
"""

from __future__ import annotations

from typing import Any

import pytest
from kubepilot_orch.llm.base import LLMProvider, Message, ProviderNotConfigured, ToolSchema
from langchain_core.messages import AIMessage


class _FakeChat:
    """Stand-in for a LangChain chat model. Returns a scripted AIMessage."""

    _script: AIMessage = AIMessage(content="")

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.bound_tools: Any = None

    def bind_tools(self, tools: Any) -> _FakeChat:
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: Any) -> AIMessage:
        return self._script


def _text_message() -> AIMessage:
    return AIMessage(
        content="root cause is OOMKilled",
        usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        response_metadata={"finish_reason": "stop"},
    )


def _tool_message() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "name": "list_pods",
                "args": {"namespace": "prod"},
                "type": "tool_call",
            }
        ],
        usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
        response_metadata={"finish_reason": "tool_calls"},
    )


# (provider name, module path of the LangChain class to patch, factory building the provider)
def _providers() -> list[tuple[str, str, Any]]:
    from kubepilot_orch.llm.anthropic import AnthropicProvider
    from kubepilot_orch.llm.azure import AzureOpenAIProvider
    from kubepilot_orch.llm.bedrock import BedrockProvider
    from kubepilot_orch.llm.ollama import OllamaProvider
    from kubepilot_orch.llm.openai import OpenAIProvider
    from kubepilot_orch.llm.vllm import VLLMProvider

    return [
        ("anthropic", "kubepilot_orch.llm.anthropic.ChatAnthropic", lambda: AnthropicProvider("k")),
        ("openai", "kubepilot_orch.llm.openai.ChatOpenAI", lambda: OpenAIProvider("k")),
        ("ollama", "kubepilot_orch.llm.ollama.ChatOllama", lambda: OllamaProvider()),
        ("vllm", "kubepilot_orch.llm.openai.ChatOpenAI", lambda: VLLMProvider()),
        (
            "bedrock",
            "kubepilot_orch.llm.bedrock.ChatBedrockConverse",
            lambda: BedrockProvider("us-east-1"),
        ),
        (
            "azure",
            "kubepilot_orch.llm.azure.AzureChatOpenAI",
            lambda: AzureOpenAIProvider("k", "https://x.openai.azure.com"),
        ),
    ]


@pytest.mark.parametrize("name,patch_target,make", _providers(), ids=[p[0] for p in _providers()])
async def test_provider_maps_text_response(
    name: str, patch_target: str, make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeChat._script = _text_message()
    monkeypatch.setattr(patch_target, _FakeChat)
    provider = make()

    assert isinstance(provider, LLMProvider)  # runtime_checkable protocol
    assert provider.name == name

    resp = await provider.chat(
        [Message(role="user", content="why is payment-service failing?")],
        model="some-model",
    )
    assert resp.content == "root cause is OOMKilled"
    assert resp.tool_calls == []
    assert resp.input_tokens == 11
    assert resp.output_tokens == 7
    assert resp.provider == name
    assert resp.model == "some-model"


@pytest.mark.parametrize("name,patch_target,make", _providers(), ids=[p[0] for p in _providers()])
async def test_provider_maps_tool_call_response(
    name: str, patch_target: str, make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeChat._script = _tool_message()
    monkeypatch.setattr(patch_target, _FakeChat)
    provider = make()

    resp = await provider.chat(
        [Message(role="user", content="inspect the cluster")],
        model="some-model",
        tools=[
            ToolSchema(
                name="list_pods",
                description="list pods",
                parameters={"type": "object", "properties": {"namespace": {"type": "string"}}},
            )
        ],
    )
    assert resp.content == ""
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.name == "list_pods"
    assert tc.arguments == {"namespace": "prod"}
    assert resp.output_tokens == 5


def test_cloud_providers_require_credentials() -> None:
    from kubepilot_orch.llm.anthropic import AnthropicProvider
    from kubepilot_orch.llm.azure import AzureOpenAIProvider
    from kubepilot_orch.llm.bedrock import BedrockProvider
    from kubepilot_orch.llm.openai import OpenAIProvider

    with pytest.raises(ProviderNotConfigured):
        AnthropicProvider(api_key=None)
    with pytest.raises(ProviderNotConfigured):
        OpenAIProvider(api_key=None)
    with pytest.raises(ProviderNotConfigured):
        BedrockProvider(region=None)
    with pytest.raises(ProviderNotConfigured):
        AzureOpenAIProvider(api_key=None, endpoint="https://x")
    with pytest.raises(ProviderNotConfigured):
        AzureOpenAIProvider(api_key="k", endpoint=None)
