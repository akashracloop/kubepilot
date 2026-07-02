"""HITL approval endpoints — RBAC + audit + status (Phase 4 W5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest_asyncio
from httpx import ASGITransport
from kubepilot_api.config import ApiSettings, KeyPolicy
from kubepilot_api.main import build_app
from kubepilot_api.repository import InMemoryInvestigationRepository, InvestigationRecord
from kubepilot_orch.state import (
    InvestigationState,
    RemediationAction,
    RemediationPlan,
)
from structlog.testing import capture_logs

_INCIDENT = uuid.UUID("55555555-5555-5555-5555-555555555555")

_KEYS = {
    "viewer-key": KeyPolicy(role="viewer", namespaces=[]),
    "operator-key": KeyPolicy(role="operator", namespaces=[]),
    "admin-key": KeyPolicy(role="admin", namespaces=[]),
}


class _FakeGraph:
    async def astream(self, initial: dict[str, Any], *, stream_mode=None, config=None):  # type: ignore[no-untyped-def]
        yield ("values", {**initial, "current_step": "completed"})


def _state_with_plan() -> InvestigationState:
    return InvestigationState(
        incident_id=_INCIDENT,
        query="why is checkout slow?",
        namespace="prod",
        service="checkout",
        started_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
        remediation_plan=RemediationPlan(
            actions=[
                RemediationAction(
                    tool="rollout_undo",
                    target="deployment/checkout",
                    namespace="prod",
                    reversibility="reversible",
                    approval_tier="operator",
                ),
                RemediationAction(
                    tool="edit_configmap",
                    target="cm/checkout",
                    namespace="prod",
                    reversibility="partial",
                    approval_tier="admin",
                ),
            ],
            # Recent, so the plan is within the approval TTL (not expired).
            generated_at=datetime.now(UTC),
        ),
        remediation_outcome="pending_approval",
    )


@pytest_asyncio.fixture
async def client() -> Any:
    settings = ApiSettings(storage="memory")
    settings.auth.keys = _KEYS
    repo = InMemoryInvestigationRepository()
    state = _state_with_plan()
    await repo.create(
        InvestigationRecord.from_initial(
            incident_id=_INCIDENT,
            query=state.query,
            namespace="prod",
            service="checkout",
            state=state,
        )
    )
    app = build_app(settings=settings, repo=repo, compiled_graph=_FakeGraph())
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield c
    await c.aclose()


def _h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


async def test_get_approval_lists_pending_plan(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/investigations/{_INCIDENT}/approval", headers=_h("operator-key"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending_approval"
    assert [a["tool"] for a in body["actions"]] == ["rollout_undo", "edit_configmap"]
    assert body["actions"][1]["approval_tier"] == "admin"


async def test_operator_approves_reversible_action_and_audits(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        r = await client.post(
            f"/investigations/{_INCIDENT}/approve",
            headers=_h("operator-key"),
            json={"action_index": 0},
        )
    assert r.status_code == 200
    # One of two actions approved → still pending overall.
    assert r.json()["status"] == "pending_approval"
    assert any(
        e.get("audit") and e["action"] == "approved_remediation" and e["decision"] == "allowed"
        for e in logs
    )


async def test_operator_cannot_approve_admin_tier_action(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        r = await client.post(
            f"/investigations/{_INCIDENT}/approve",
            headers=_h("operator-key"),
            json={"action_index": 1},  # admin-tier edit_configmap
        )
    assert r.status_code == 403
    assert any(
        e.get("audit") and e["decision"] == "denied" and e["reason"] == "insufficient_approval_tier"
        for e in logs
    )


async def test_admin_approves_both_actions_to_approved(client: httpx.AsyncClient) -> None:
    await client.post(
        f"/investigations/{_INCIDENT}/approve", headers=_h("admin-key"), json={"action_index": 0}
    )
    r = await client.post(
        f"/investigations/{_INCIDENT}/approve", headers=_h("admin-key"), json={"action_index": 1}
    )
    assert r.json()["status"] == "approved"


async def test_reject_marks_plan_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post(
        f"/investigations/{_INCIDENT}/reject", headers=_h("operator-key"), json={"action_index": 0}
    )
    assert r.json()["status"] == "rejected"


async def test_viewer_cannot_approve(client: httpx.AsyncClient) -> None:
    r = await client.post(
        f"/investigations/{_INCIDENT}/approve", headers=_h("viewer-key"), json={"action_index": 0}
    )
    assert r.status_code == 403


# ---- kill switch (admin-only) ---------------------------------------------


async def test_kill_switch_admin_only(client: httpx.AsyncClient) -> None:
    from kubepilot_orch.remediation import executor

    executor.set_kill_switch(False)
    # Operator is denied.
    denied = await client.post(
        "/remediation/kill-switch", headers=_h("operator-key"), json={"enabled": True}
    )
    assert denied.status_code == 403
    assert executor.kill_switch_active() is False

    # Admin can enable it.
    ok = await client.post(
        "/remediation/kill-switch", headers=_h("admin-key"), json={"enabled": True}
    )
    assert ok.status_code == 200 and ok.json()["enabled"] is True
    assert executor.kill_switch_active() is True

    # Reflected on GET; then reset.
    got = await client.get("/remediation/kill-switch", headers=_h("operator-key"))
    assert got.json()["enabled"] is True
    executor.set_kill_switch(False)
