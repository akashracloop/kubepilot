"""Post-remediation validation (Phase 4 W9).

After a remediation executes, KubePilot re-checks the incident's signals to decide
whether the fix actually worked, and closes the loop accordingly:

- **improved** → the symptoms cleared → mark the incident **closed**.
- **regressed** → the action made things worse → **auto-rollback** the reversible
  actions (W8) and **reopen** the incident.
- **unchanged** → the action was safe but didn't help → **reopen** (no rollback;
  nothing got worse) so a human takes the next step.

The comparison is between the incident's baseline signals (captured pre-execution)
and the post-remediation signals. This module is pure decision logic; the live
signal fetch (mcp-prom / mcp-k8s over the watch window) is wired in the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.remediation.rollback import assess_regression, auto_rollback
from kubepilot_orch.state import ExecutionRecord, RollbackRecord

log = structlog.get_logger(__name__)

# Minimum error-rate drop to call a fix "improved".
DEFAULT_IMPROVE_ERROR_DROP = 0.05


def assess_outcome(
    before: dict[str, float],
    after: dict[str, float],
    *,
    improve_error_drop: float = DEFAULT_IMPROVE_ERROR_DROP,
) -> str:
    """Classify the remediation's effect: 'regressed' | 'improved' | 'unchanged'."""
    if assess_regression(before, after):
        return "regressed"
    error_drop = before.get("error_rate", 0.0) - after.get("error_rate", 0.0)
    restarts_stopped = after.get("restarts", 0.0) <= before.get("restarts", 0.0)
    if error_drop >= improve_error_drop and restarts_stopped:
        return "improved"
    return "unchanged"


@dataclass
class ValidationResult:
    outcome: str  # "closed" | "reopened"
    kind: str  # "improved" | "regressed" | "unchanged"
    reason: str
    rollbacks: list[RollbackRecord] = field(default_factory=list)


async def finalize_remediation(
    executions: list[ExecutionRecord],
    before: dict[str, float],
    after: dict[str, float],
    *,
    mcp_write: MCPClient,
) -> ValidationResult:
    """Validate the fix; rollback + reopen on regression, close on improvement."""
    kind = assess_outcome(before, after)
    if kind == "regressed":
        rollbacks = await auto_rollback(executions, mcp_write=mcp_write, regressed=True)
        log.info("remediation_regressed", rollbacks=len(rollbacks))
        return ValidationResult(
            outcome="reopened",
            kind=kind,
            reason="post-remediation regression — reverted reversible actions",
            rollbacks=rollbacks,
        )
    if kind == "improved":
        return ValidationResult(outcome="closed", kind=kind, reason="fix confirmed by re-check")
    return ValidationResult(
        outcome="reopened", kind=kind, reason="no improvement after remediation"
    )
