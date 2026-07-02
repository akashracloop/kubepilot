"""UI-editable settings API (GET effective / PUT admin-gated + persist)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest_asyncio
from httpx import ASGITransport
from kubepilot_api.config import ApiSettings, KeyPolicy
from kubepilot_api.main import build_app
from kubepilot_api.repository import InMemoryInvestigationRepository

_KEYS = {
    "viewer-key": KeyPolicy(role="viewer", namespaces=[]),
    "admin-key": KeyPolicy(role="admin", namespaces=[]),
}


class _FakeGraph:
    async def astream(self, *a: Any, **k: Any):  # pragma: no cover - unused here
        yield ("values", {})


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    settings = ApiSettings(storage="memory")
    settings.auth.keys = _KEYS
    app = build_app(
        settings=settings, repo=InMemoryInvestigationRepository(), compiled_graph=_FakeGraph()
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_get_settings_returns_grouped_effective(client: httpx.AsyncClient) -> None:
    r = await client.get("/settings", headers={"X-API-Key": "viewer-key"})
    assert r.status_code == 200
    body = r.json()
    assert "features" in body["groups"] and "llm" in body["groups"]
    assert "remediation" in body["groups"] and "prompts" in body["groups"]
    # readonly infra facts + kill switch present.
    assert any(f["label"].startswith("MCP") for f in body["readonly"])
    assert body["kill_switch"] is False
    # a known feature toggle is present with a boolean value.
    critic = next(f for f in body["groups"]["features"] if f["key"] == "features.critic_enabled")
    assert isinstance(critic["value"], bool)


async def test_put_requires_admin(client: httpx.AsyncClient) -> None:
    r = await client.put(
        "/settings",
        headers={"X-API-Key": "viewer-key"},
        json={"overrides": {"features.critic_enabled": False}},
    )
    assert r.status_code == 403


async def test_put_persists_and_reflects_override(client: httpx.AsyncClient) -> None:
    r = await client.put(
        "/settings",
        headers={"X-API-Key": "admin-key"},
        json={"overrides": {"features.memory_enabled": False, "llm.default_provider": "openai"}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # GET reflects the persisted overrides.
    got = await client.get("/settings", headers={"X-API-Key": "admin-key"})
    groups = got.json()["groups"]
    mem = next(f for f in groups["features"] if f["key"] == "features.memory_enabled")
    prov = next(f for f in groups["llm"] if f["key"] == "llm.default_provider")
    assert mem["value"] is False and mem["overridden"] is True
    assert prov["value"] == "openai"


async def test_put_rejects_unknown_key(client: httpx.AsyncClient) -> None:
    r = await client.put(
        "/settings",
        headers={"X-API-Key": "admin-key"},
        json={"overrides": {"features.bogus": True}},
    )
    assert r.status_code == 422


async def test_kill_switch_admin_toggle(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/settings/kill-switch", headers={"X-API-Key": "admin-key"}, json={"enabled": True}
    )
    assert r.status_code == 200 and r.json()["kill_switch"] is True
    # reset so we don't leak process-global state to other tests.
    await client.post(
        "/settings/kill-switch", headers={"X-API-Key": "admin-key"}, json={"enabled": False}
    )
