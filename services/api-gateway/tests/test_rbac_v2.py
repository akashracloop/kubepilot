"""RBAC v2 — role hierarchy authz matrix + audit export (Phase 3 W11)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from kubepilot_api.auth import Principal
from kubepilot_api.config import ApiSettings, KeyPolicy
from kubepilot_api.main import build_app
from kubepilot_api.repository import InMemoryInvestigationRepository
from structlog.testing import capture_logs

# ---- Authz matrix on the Principal model ----------------------------------


@pytest.mark.parametrize(
    ("role", "can_view", "can_investigate", "is_admin"),
    [
        ("viewer", True, False, False),
        ("investigator", True, True, False),
        ("operator", True, True, False),
        ("admin", True, True, True),
        ("bogus", False, False, False),  # unknown role denies everything
    ],
)
def test_role_capability_matrix(
    role: str, can_view: bool, can_investigate: bool, is_admin: bool
) -> None:
    p = Principal(role=role, namespaces=[])
    assert p.can_view() is can_view
    assert p.can_investigate() is can_investigate
    assert p.is_admin() is is_admin


def test_namespace_scoping_by_role() -> None:
    scoped_viewer = Principal(role="viewer", namespaces=["prod"])
    assert scoped_viewer.allows_namespace("prod")
    assert not scoped_viewer.allows_namespace("staging")

    scoped_investigator = Principal(role="investigator", namespaces=["prod"])
    assert not scoped_investigator.allows_namespace("staging")

    # Operator/admin transcend namespace scoping even with a scope set.
    scoped_operator = Principal(role="operator", namespaces=["prod"])
    assert scoped_operator.allows_namespace("staging")
    scoped_admin = Principal(role="admin", namespaces=["prod"])
    assert scoped_admin.allows_namespace("anything")


# ---- HTTP-level: operator sees everything; audit is emitted ---------------


class _FakeGraph:
    async def astream(self, initial: dict[str, Any], *, stream_mode=None, config=None):  # type: ignore[no-untyped-def]
        yield ("updates", {"supervisor": {}})
        yield ("values", {**initial, "current_step": "completed"})


_KEYS = {
    "viewer-key": KeyPolicy(role="viewer", namespaces=["prod"]),
    "prod-inv-key": KeyPolicy(role="investigator", namespaces=["prod"]),
    "operator-key": KeyPolicy(role="operator", namespaces=["prod"]),  # scope ignored
}


def _app() -> Any:
    settings = ApiSettings(storage="memory")
    settings.auth.keys = _KEYS
    return build_app(
        settings=settings, repo=InMemoryInvestigationRepository(), compiled_graph=_FakeGraph()
    )


@pytest_asyncio.fixture
async def client() -> Any:
    c = httpx.AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test")
    yield c
    await c.aclose()


def _body(ns: str) -> dict[str, str]:
    return {"query": "why failing?", "namespace": ns, "service": "svc"}


async def test_operator_can_investigate_any_namespace(client: httpx.AsyncClient) -> None:
    for ns in ("prod", "staging", "dev"):
        r = await client.post(
            "/investigations", headers={"X-API-Key": "operator-key"}, json=_body(ns)
        )
        assert r.status_code == 202, ns


async def test_scoped_investigator_denied_out_of_scope(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/investigations", headers={"X-API-Key": "prod-inv-key"}, json=_body("staging")
    )
    assert r.status_code == 403


async def test_audit_event_emitted_on_allowed_create(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        r = await client.post(
            "/investigations", headers={"X-API-Key": "prod-inv-key"}, json=_body("prod")
        )
        assert r.status_code == 202
    audits = [e for e in logs if e.get("audit")]
    assert any(
        e["action"] == "create_investigation"
        and e["decision"] == "allowed"
        and e["namespace"] == "prod"
        for e in audits
    )
    assert any(e["actor_role"] == "investigator" for e in audits)


async def test_audit_event_emitted_on_denied_create(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        await client.post(
            "/investigations", headers={"X-API-Key": "viewer-key"}, json=_body("prod")
        )
    denied = [e for e in logs if e.get("audit") and e["decision"] == "denied"]
    assert denied
    assert denied[0]["action"] == "create_investigation"
    assert denied[0]["reason"] == "insufficient_role"
