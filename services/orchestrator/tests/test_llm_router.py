"""Tests for the LLM router (provider selection, role binding).

Real provider calls are excluded from CI via the ``live_llm`` marker.
"""

from __future__ import annotations

import pytest
from kubepilot_orch.config import LLMRoleBinding
from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderNotConfigured,
    Role,
    ToolSchema,
)
from kubepilot_orch.llm.router import LLMRouter
from pydantic import BaseModel


class FakeProvider:
    """In-memory stand-in for an LLMProvider used in unit tests."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict] = []

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
        self.calls.append({"model": model, "messages": [m.content for m in messages]})
        return LLMResponse(
            content="ok",
            model=model,
            provider=self.name,
            input_tokens=10,
            output_tokens=5,
        )


@pytest.mark.asyncio
async def test_router_dispatches_to_bound_provider() -> None:
    fake = FakeProvider()
    router = LLMRouter(
        providers={"fake": fake},  # type: ignore[dict-item]
        role_bindings={
            Role.ROUTING: LLMRoleBinding(provider="fake", model="fake-mini"),
            Role.ANALYSIS: LLMRoleBinding(provider="fake", model="fake-pro"),
        },
    )

    r1 = await router.chat(role=Role.ROUTING, messages=[Message(role="user", content="hi")])
    r2 = await router.chat(role=Role.ANALYSIS, messages=[Message(role="user", content="hi")])

    assert r1.model == "fake-mini"
    assert r2.model == "fake-pro"
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_router_raises_when_provider_not_loaded() -> None:
    router = LLMRouter(
        providers={},
        role_bindings={Role.ANALYSIS: LLMRoleBinding(provider="missing", model="m")},
    )
    with pytest.raises(ProviderNotConfigured, match="missing"):
        await router.chat(role=Role.ANALYSIS, messages=[Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_router_raises_when_role_unbound() -> None:
    router = LLMRouter(providers={}, role_bindings={})
    with pytest.raises(ProviderNotConfigured, match="role=analysis"):
        await router.chat(role=Role.ANALYSIS, messages=[Message(role="user", content="hi")])


def test_fake_provider_satisfies_protocol() -> None:
    # Protocol conformance — runtime_checkable on LLMProvider lets us assert this.
    fake = FakeProvider()
    assert isinstance(fake, LLMProvider)
