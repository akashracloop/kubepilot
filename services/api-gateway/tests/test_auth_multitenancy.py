"""W10: per-key roles + namespace allowlists (light multi-tenancy)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest_asyncio
from httpx import ASGITransport
from kubepilot_api.config import ApiSettings, KeyPolicy
from kubepilot_api.main import build_app
from kubepilot_api.repository import InMemoryInvestigationRepository


class _FakeGraph:
    """Minimal graph: emits one progress update then echoes a completed state."""

    async def astream(self, initial: dict[str, Any], *, stream_mode=None, config=None):  # type: ignore[no-untyped-def]
        yield ("updates", {"supervisor": {}})
        yield ("values", {**initial, "current_step": "completed"})


def _app(**auth: Any) -> Any:
    settings = ApiSettings(storage="memory")
    for k, v in auth.items():
        setattr(settings.auth, k, v)
    return build_app(
        settings=settings, repo=InMemoryInvestigationRepository(), compiled_graph=_FakeGraph()
    )


@pytest_asyncio.fixture
async def client_factory():  # type: ignore[no-untyped-def]
    clients: list[httpx.AsyncClient] = []

    async def make(**auth: Any) -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=ASGITransport(app=_app(**auth)), base_url="http://test")
        clients.append(c)
        return c

    yield make
    for c in clients:
        await c.aclose()


_KEYS = {
    "viewer-key": KeyPolicy(role="viewer", namespaces=[]),
    "prod-key": KeyPolicy(role="investigator", namespaces=["prod"]),
    "admin-key": KeyPolicy(role="investigator", namespaces=[]),
}


def _body(ns: str = "prod") -> dict[str, str]:
    return {"query": "why failing?", "namespace": ns, "service": "payment-service"}


async def test_viewer_cannot_trigger_but_can_read(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = await client_factory(keys=_KEYS)
    post = await client.post("/investigations", headers={"X-API-Key": "viewer-key"}, json=_body())
    assert post.status_code == 403
    # …but a viewer may list.
    listed = await client.get("/investigations", headers={"X-API-Key": "viewer-key"})
    assert listed.status_code == 200


async def test_scoped_key_denied_other_namespace(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = await client_factory(keys=_KEYS)
    ok = await client.post("/investigations", headers={"X-API-Key": "prod-key"}, json=_body("prod"))
    assert ok.status_code == 202
    denied = await client.post(
        "/investigations", headers={"X-API-Key": "prod-key"}, json=_body("staging")
    )
    assert denied.status_code == 403


async def test_unknown_key_401(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = await client_factory(keys=_KEYS)
    r = await client.post("/investigations", headers={"X-API-Key": "nope"}, json=_body())
    assert r.status_code == 401


async def test_admin_all_namespaces(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = await client_factory(keys=_KEYS)
    for ns in ("prod", "staging", "dev"):
        r = await client.post("/investigations", headers={"X-API-Key": "admin-key"}, json=_body(ns))
        assert r.status_code == 202, ns


async def test_open_dev_mode_when_no_auth_configured(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = await client_factory()  # no api_key, no keys
    r = await client.post("/investigations", json=_body())
    assert r.status_code == 202


async def test_scoped_list_filters_out_of_scope(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = await client_factory(keys=_KEYS)
    # admin creates one investigation in staging.
    await client.post("/investigations", headers={"X-API-Key": "admin-key"}, json=_body("staging"))
    # prod-scoped key should not see it.
    listed = (await client.get("/investigations", headers={"X-API-Key": "prod-key"})).json()
    assert all(item["namespace"] == "prod" for item in listed["items"])
