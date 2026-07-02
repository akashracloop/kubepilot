"""HITL approval logic — RBAC + plan status + expiry (Phase 4 W5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from kubepilot_orch.remediation import approval
from kubepilot_orch.state import Approval, RemediationAction, RemediationPlan


def _plan(*tiers: str) -> RemediationPlan:
    return RemediationPlan(
        actions=[
            RemediationAction(
                tool="scale", target="deployment/x", namespace="prod", approval_tier=t
            )
            for t in tiers
        ],
        generated_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
    )


def _decide(idx: int, decision: str, role: str) -> Approval:
    return approval.build_approval(action_index=idx, decision=decision, approver_role=role)


# ---- approver RBAC --------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "tier", "ok"),
    [
        ("operator", "operator", True),
        ("admin", "operator", True),
        ("admin", "admin", True),
        ("operator", "admin", False),  # operator can't approve admin-tier
        ("investigator", "operator", False),
        ("viewer", "operator", False),
        ("bogus", "operator", False),  # unknown role denied
    ],
)
def test_can_approve_matrix(role: str, tier: str, ok: bool) -> None:
    assert approval.can_approve(role, tier) is ok


def test_authorize_uses_action_tier() -> None:
    admin_action = RemediationAction(
        tool="edit_configmap", target="cm/x", namespace="prod", approval_tier="admin"
    )
    assert approval.authorize(admin_action, "admin") is True
    assert approval.authorize(admin_action, "operator") is False


# ---- plan status ----------------------------------------------------------


def test_pending_until_all_approved() -> None:
    plan = _plan("operator", "operator")
    assert approval.plan_status(plan, []) == "pending_approval"
    assert approval.plan_status(plan, [_decide(0, "approved", "operator")]) == "pending_approval"
    both = [_decide(0, "approved", "operator"), _decide(1, "approved", "operator")]
    assert approval.plan_status(plan, both) == "approved"


def test_mixed_decisions_are_partial() -> None:
    # A mix of approve + reject → "partial": execute the approved subset, skip the
    # rejected one (per-action approval, not all-or-nothing).
    plan = _plan("operator", "operator")
    decisions = [_decide(0, "approved", "operator"), _decide(1, "rejected", "operator")]
    assert approval.plan_status(plan, decisions) == "partial"
    assert approval.approved_action_indices(plan, decisions) == [0]


def test_all_rejected_rejects_the_plan() -> None:
    plan = _plan("operator", "operator")
    decisions = [_decide(0, "rejected", "operator"), _decide(1, "rejected", "operator")]
    assert approval.plan_status(plan, decisions) == "rejected"


def test_latest_decision_wins() -> None:
    plan = _plan("operator")
    decisions = [_decide(0, "rejected", "operator"), _decide(0, "approved", "admin")]
    assert approval.plan_status(plan, decisions) == "approved"


def test_expiry_lapses_undecided_plan() -> None:
    plan = _plan("operator")
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC) + timedelta(hours=1)
    assert approval.plan_status(plan, [], now=now, generated_at=plan.generated_at) == "expired"
    # An approved plan is not expired even past the TTL.
    approved = [_decide(0, "approved", "operator")]
    assert (
        approval.plan_status(plan, approved, now=now, generated_at=plan.generated_at) == "approved"
    )


def test_empty_plan_is_no_action() -> None:
    assert approval.plan_status(RemediationPlan(actions=[]), []) == "no_action"


def test_approved_indices_is_execution_allowlist() -> None:
    plan = _plan("operator", "operator", "operator")
    decisions = [_decide(0, "approved", "operator"), _decide(2, "approved", "operator")]
    assert approval.approved_action_indices(plan, decisions) == [0, 2]
