"""HITL approval logic (Phase 4 W5) — who may approve, and the plan's status.

The graph interrupts **before** executing a remediation plan; a human then
approves or rejects via the API. This module holds the pure decision logic:

- **Approver RBAC** — an approver's role must be at least the action's required
  tier (operator for reversible actions, admin for partial/irreversible).
- **Plan status** — from the recorded per-action decisions (+ an expiry TTL):
  ``approved`` (every action approved), ``rejected`` (every action rejected),
  ``partial`` (all decided, a mix — execute the approved subset), ``expired``
  (undecided past the TTL), or ``pending_approval``.

Fail-safe: an unknown role, a missing decision, or an expired plan all resolve to
*not approved* — execution never proceeds without a fresh, authorized approval.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kubepilot_orch.state import Approval, RemediationAction, RemediationPlan

# How long an unactioned plan stays approvable before it lapses.
APPROVAL_TTL = timedelta(minutes=30)

# Role privilege ranks (mirror api-gateway RBAC v2).
_ROLE_RANK = {"viewer": 0, "investigator": 1, "operator": 2, "admin": 3}


def can_approve(approver_role: str, approval_tier: str) -> bool:
    """True when ``approver_role`` is at least the action's required tier."""
    return _ROLE_RANK.get(approver_role, -1) >= _ROLE_RANK.get(approval_tier, 999)


def authorize(action: RemediationAction, approver_role: str) -> bool:
    """Whether ``approver_role`` may approve/reject this specific action."""
    return can_approve(approver_role, action.approval_tier)


def build_approval(
    *,
    action_index: int | None,
    decision: str,
    approver_role: str,
    approver: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> Approval:
    return Approval(
        action_index=action_index,
        decision=decision,
        approver_role=approver_role,
        approver=approver,
        reason=reason,
        decided_at=now or datetime.now(UTC),
    )


def _latest_decisions(approvals: list[Approval]) -> dict[int, str]:
    """Most-recent decision per action index (later entries win)."""
    out: dict[int, str] = {}
    for a in approvals:
        if a.action_index is not None:
            out[a.action_index] = a.decision
    return out


def plan_status(
    plan: RemediationPlan,
    approvals: list[Approval],
    *,
    now: datetime | None = None,
    generated_at: datetime | None = None,
    ttl: timedelta = APPROVAL_TTL,
) -> str:
    """Resolve the plan's approval status from its per-action decisions + expiry."""
    if not plan.actions:
        return "no_action"
    now = now or datetime.now(UTC)
    latest = _latest_decisions(approvals)
    decisions = [latest.get(i) for i in range(len(plan.actions))]

    # Every action has a terminal decision → the plan is ready to execute the
    # approved subset. All-approved / all-rejected are the pure cases; a mix is
    # "partial" (execute the approved actions, skip the rejected ones).
    if all(d in ("approved", "rejected") for d in decisions):
        if all(d == "approved" for d in decisions):
            return "approved"
        if all(d == "rejected" for d in decisions):
            return "rejected"
        return "partial"
    # Some action still undecided: lapse the plan once past the TTL (fail-safe —
    # no stale auto-approval).
    if generated_at is not None and now - generated_at > ttl:
        return "expired"
    return "pending_approval"


def approved_action_indices(plan: RemediationPlan, approvals: list[Approval]) -> list[int]:
    """Indices of actions with a current ``approved`` decision (execution allow-list)."""
    latest = _latest_decisions(approvals)
    return [i for i in range(len(plan.actions)) if latest.get(i) == "approved"]
