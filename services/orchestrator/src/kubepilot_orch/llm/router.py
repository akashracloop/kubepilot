"""Role-based router over multiple LLM providers.

Config example (values.yaml):

    llm:
      default_provider: anthropic
      roles:
        routing:       { provider: anthropic, model: claude-haiku-4-5-20251001 }
        analysis:      { provider: anthropic, model: claude-sonnet-4-6 }
        summarization: { provider: anthropic, model: claude-haiku-4-5-20251001 }

The router holds a dict of instantiated providers and dispatches per-role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderNotConfigured,
    Role,
    ToolSchema,
)

if TYPE_CHECKING:
    from kubepilot_orch.config import LLMRoleBinding

log = structlog.get_logger(__name__)


class LLMRouter:
    """Routes a chat() call to the right provider+model based on the call role."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        role_bindings: dict[Role, LLMRoleBinding],
    ) -> None:
        self._providers = providers
        self._role_bindings = role_bindings

    async def chat(
        self,
        role: Role,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        binding = self._role_bindings.get(role)
        if binding is None:
            # Fall back to the analysis (strong-reasoning) role for any role that
            # isn't explicitly configured — so a partial roles map (e.g. one missing
            # the Phase 3 'critique' role) degrades gracefully instead of failing the
            # whole investigation.
            binding = self._role_bindings.get(Role.ANALYSIS)
            if binding is None:
                raise ProviderNotConfigured(
                    f"No LLM binding configured for role={role.value} and no analysis fallback"
                )
            log.warning("llm_role_fallback", requested=role.value, fell_back_to="analysis")

        provider = self._providers.get(binding.provider)
        if provider is None:
            raise ProviderNotConfigured(
                f"Provider {binding.provider!r} required by role {role.value} is not loaded"
            )

        return await provider.chat(
            messages=messages,
            model=binding.model,
            tools=tools,
            response_schema=response_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @property
    def loaded_providers(self) -> list[str]:
        return sorted(self._providers.keys())
