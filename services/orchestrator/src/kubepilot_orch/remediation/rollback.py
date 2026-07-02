"""Auto-rollback (Phase 4 W8).

After a remediation executes, its effect is watched for a window (N minutes). If a
**regression** attributable to the action appears, the action is **automatically
reverted** — and only reversible actions are ever auto-taken, so there is always a
safe inverse:

- ``scale``       → scale back to the captured pre-execution replica count
- ``patch_image`` → patch back to the captured pre-execution image
- ``cordon``      → ``uncordon`` (self-inverting)
- ``uncordon``    → ``cordon``

Actions without a clean inverse (``rollout_undo``, ``rollout_restart``,
``restart_pod``) are not auto-reverted — reverting a rollback or a restart isn't
meaningful; those escalate to a human instead. Rollback runs once, never loops.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from kubepilot_orch.mcp.client import MCPClient, MCPError
from kubepilot_orch.state import ExecutionRecord, RemediationAction, RollbackRecord

log = structlog.get_logger(__name__)
_audit_log = structlog.get_logger("kubepilot.audit")

# Default regression thresholds for the post-execution watch window.
DEFAULT_ERROR_RATE_INCREASE = 0.10  # +10 percentage points of error rate
DEFAULT_RESTART_INCREASE = 1  # any new restart


def assess_regression(
    before: dict[str, float],
    after: dict[str, float],
    *,
    error_rate_increase: float = DEFAULT_ERROR_RATE_INCREASE,
    restart_increase: int = DEFAULT_RESTART_INCREASE,
) -> bool:
    """True when post-execution signals are meaningfully worse than pre-execution.

    ``before``/``after`` are signal snapshots, e.g. ``{"error_rate": 0.02,
    "restarts": 3}``. Conservative: any single regressed dimension trips it.
    """
    error_regressed = (
        after.get("error_rate", 0.0) - before.get("error_rate", 0.0) >= error_rate_increase
    )
    restart_regressed = after.get("restarts", 0.0) - before.get("restarts", 0.0) >= restart_increase
    return error_regressed or restart_regressed


def inverse_action(record: ExecutionRecord) -> RemediationAction | None:
    """The action that reverts ``record``, or None if it isn't auto-revertible."""
    pre = record.pre_state or {}
    match record.tool:
        case "scale" if "replicas" in pre:
            return _act("scale", record, {"replicas": pre["replicas"]})
        case "patch_image" if "image" in pre:
            args = {"image": pre["image"]}
            if "container" in pre:
                args["container"] = pre["container"]
            return _act("patch_image", record, args)
        case "cordon":
            return _act("uncordon", record, {})
        case "uncordon":
            return _act("cordon", record, {})
        case _:
            return None


def _act(tool: str, record: ExecutionRecord, arguments: dict) -> RemediationAction:  # type: ignore[type-arg]
    return RemediationAction(
        tool=tool,
        target=record.target,
        namespace=record.namespace,
        arguments=arguments,
        rationale=f"auto-rollback of {record.tool} on {record.target}",
        reversibility="reversible",
    )


def _audit(record: RollbackRecord, target: str, namespace: str) -> None:
    _audit_log.info(
        "audit",
        audit=True,
        action="auto_rollback",
        actor_role="system",
        resource=target,
        namespace=namespace,
        decision="rolled_back" if record.status == "succeeded" else "rollback_failed",
        status=record.status,
        reason=record.reason,
    )


async def auto_rollback(
    executions: list[ExecutionRecord],
    *,
    mcp_write: MCPClient,
    regressed: bool = True,
    now: datetime | None = None,
) -> list[RollbackRecord]:
    """Revert the reversible executed actions when a regression was detected.

    ``regressed=False`` is a no-op (the fix held). Only actions that actually ran
    (``succeeded``/``dry_run``) and have a computable inverse are reverted.
    """
    if not regressed:
        return []
    now = now or datetime.now(UTC)
    rollbacks: list[RollbackRecord] = []

    for rec in executions:
        if rec.status not in ("succeeded", "dry_run"):
            continue
        inverse = inverse_action(rec)
        if inverse is None:
            log.info("rollback_not_applicable", tool=rec.tool, target=rec.target)
            continue
        try:
            await mcp_write.invoke(
                inverse.tool,
                {"namespace": inverse.namespace, "target": inverse.target, **inverse.arguments},
            )
            rb = RollbackRecord(
                action_index=rec.action_index,
                reason="post-exec regression",
                status="succeeded",
                at=now,
            )
        except MCPError as e:
            rb = RollbackRecord(
                action_index=rec.action_index,
                reason=f"post-exec regression; rollback failed: {e}",
                status="failed",
                at=now,
            )
        _audit(rb, rec.target, rec.namespace)
        rollbacks.append(rb)

    return rollbacks
