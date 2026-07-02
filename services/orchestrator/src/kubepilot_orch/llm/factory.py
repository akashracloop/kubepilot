"""Factory that builds a LLMRouter from config.

Only loads providers that are actually referenced by a role binding — this lets
an air-gapped install run with just Ollama loaded, with no Anthropic/OpenAI
keys configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kubepilot_orch.llm.base import LLMProvider, ProviderNotConfigured
from kubepilot_orch.llm.router import LLMRouter

if TYPE_CHECKING:
    from kubepilot_orch.config import OrchestratorSettings


def build_router(settings: OrchestratorSettings) -> LLMRouter:
    needed_providers = {b.provider for b in settings.llm.roles.values()}

    providers: dict[str, LLMProvider] = {}
    for name in needed_providers:
        providers[name] = _instantiate(name, settings)

    return LLMRouter(providers=providers, role_bindings=settings.llm.roles)


def _instantiate(name: str, settings: OrchestratorSettings) -> LLMProvider:
    match name:
        case "anthropic":
            from kubepilot_orch.llm.anthropic import AnthropicProvider

            return AnthropicProvider(api_key=settings.llm.anthropic_api_key)
        case "openai":
            from kubepilot_orch.llm.openai import OpenAIProvider

            return OpenAIProvider(api_key=settings.llm.openai_api_key)
        case "ollama":
            from kubepilot_orch.llm.ollama import OllamaProvider

            return OllamaProvider(base_url=settings.llm.ollama_base_url)
        case "vllm":
            from kubepilot_orch.llm.vllm import VLLMProvider

            return VLLMProvider(base_url=settings.llm.vllm_base_url)
        case "bedrock":
            from kubepilot_orch.llm.bedrock import BedrockProvider

            return BedrockProvider(region=settings.llm.bedrock_region)
        case "azure":
            from kubepilot_orch.llm.azure import AzureOpenAIProvider

            return AzureOpenAIProvider(
                api_key=settings.llm.azure_api_key, endpoint=settings.llm.azure_endpoint
            )
        case _:
            raise ProviderNotConfigured(f"Unknown LLM provider: {name!r}")
