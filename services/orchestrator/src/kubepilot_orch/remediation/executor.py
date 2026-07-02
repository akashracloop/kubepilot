"""Remediation execution engine (Phase 4 W7) — the only path to a cluster write.

Every approved action passes through, in order: **kill-switch check → policy
check → blast-radius gate → mcp-k8s-write invoke → per-action audit**. Nothing
executes that isn't (a) approved, (b) allowed by policy, and (c) below the
blast-radius caps. A process-global **kill switch** halts all execution
immediately; every attempt — executed, skipped, or failed — is audited.

Fail-safe: a missing policy denies everything; an MCP error records a ``failed``
outcome (never a silent success); the write server itself is dry-run until its
apply flag is turned on (W11 kind sandbox), so the engine is exercisable without
touching a real cluster.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from kubepilot_orch.mcp.client import MCPClient, MCPError
from kubepilot_orch.remediation import approval
from kubepilot_orch.remediation.policy import RemediationPolicy
from kubepilot_orch.state import Approval, ExecutionRecord, RemediationPlan

log = structlog.get_logger(__name__)
_audit_log = structlog.get_logger("kubepilot.audit")

# Process-global kill switch. Set via the API (POST /remediation/kill-switch).
_KILL = {"on": False}


def set_kill_switch(on: bool) -> None:
    _KILL["on"] = bool(on)
    log.warning("remediation_kill_switch", enabled=_KILL["on"])


def kill_switch_active() -> bool:
    return _KILL["on"]


def _audit(record: ExecutionRecord, *, role: str, decision: str, reason: str | None = None) -> None:
    """Emit a tamper-evident audit event for one execution attempt."""
    _audit_log.info(
        "audit",
        audit=True,
        action="execute_remediation",
        actor_role=role,
        resource=f"{record.tool}/{record.target}",
        namespace=record.namespace,
        decision=decision,  # "executed" | "skipped" | "failed"
        dry_run=record.dry_run,
        status=record.status,
        reason=reason,
    )


async def execute_plan(
    plan: RemediationPlan,
    approvals: list[Approval],
    *,
    mcp_write: MCPClient,
    policy: RemediationPolicy | None,
    pre_state_fn: Callable[[Any], Awaitable[dict[str, Any] | None]] | None = None,
    now: datetime | None = None,
) -> list[ExecutionRecord]:
    """Execute the approved, in-policy actions of a plan. Returns audited records."""
    now = now or datetime.now(UTC)
    approved = approval.approved_action_indices(plan, approvals)
    role_by_index = {
        a.action_index: (a.approver_role or "operator")
        for a in approvals
        if a.decision == "approved" and a.action_index is not None
    }
    records: list[ExecutionRecord] = []

    for i in approved:
        action = plan.actions[i]
        role = role_by_index.get(i, "operator")
        rec = ExecutionRecord(
            action_index=i,
            tool=action.tool,
            target=action.target,
            namespace=action.namespace,
            status="skipped",
            dry_run=True,
            started_at=now,
        )

        # 1. Kill switch — halt everything, immediately.
        if kill_switch_active():
            rec.output = "kill switch active — execution halted"
            _audit(rec, role=role, decision="skipped", reason="kill_switch")
            records.append(rec)
            continue

        # 2. Policy gate (default-deny) with the approver's role + blast-radius caps.
        br = action.estimated_blast_radius
        decision = (
            policy.evaluate(
                action=action.tool,
                namespace=action.namespace,
                role=role,
                reversibility=action.reversibility,
                blast_radius_pods=br.pods_affected if br else None,
                blast_radius_traffic=br.traffic_percent if br else None,
            )
            if policy is not None
            else None
        )
        if decision is None or not decision.allowed:
            reason = decision.reason if decision else "no policy configured"
            rec.output = f"policy denied: {reason}"
            _audit(rec, role=role, decision="skipped", reason="policy_denied")
            records.append(rec)
            continue

        # 3. Capture pre-execution state (for auto-rollback) before mutating.
        if pre_state_fn is not None:
            try:
                rec.pre_state = await pre_state_fn(action)
            except Exception as e:  # capture failure must not block execution
                log.warning("pre_state_capture_failed", tool=action.tool, error=str(e))

        # 4. Execute via the write MCP (dry-run until its apply flag is on).
        try:
            result = await mcp_write.invoke(
                action.tool,
                {"namespace": action.namespace, "target": action.target, **action.arguments},
            )
        except MCPError as e:
            rec.status = "failed"
            rec.output = f"write MCP error: {e}"
            rec.finished_at = datetime.now(UTC)
            _audit(rec, role=role, decision="failed", reason="mcp_error")
            records.append(rec)
            continue

        applied = bool(result.get("applied")) if isinstance(result, dict) else False
        rec.status = "succeeded" if applied else "dry_run"
        rec.dry_run = not applied
        rec.output = _result_text(result)
        rec.finished_at = datetime.now(UTC)
        _audit(rec, role=role, decision="executed")
        records.append(rec)

    return records


def _result_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("preview") or result.get("note") or result)
    return str(result)
