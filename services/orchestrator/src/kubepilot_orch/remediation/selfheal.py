"""Self-healing loops (Phase 4 W10) — opt-in autonomous fixes for known-safe cases.

For a small, fixed set of **known-safe, low-blast** patterns, KubePilot may act
**without interactive approval** — but *every other* safety gate still applies:
the execution policy (default-deny), blast-radius caps, the kill switch, per-action
audit, and post-remediation auto-rollback. Nothing is autonomous by default; an
operator enables each pattern individually.

Shipped patterns (all reversible):
- ``imagepull_revert``   — ImagePullBackOff (usually a bad image tag) → roll the
  deployment back to its previous, known-good revision.
- ``crashloop_restart``  — a single crash-looping pod with a transient cause →
  delete it so its controller recreates it (blast radius: 1 pod).

Self-heal runs under a configured actor **role** (default ``operator``), so the
policy engine gates it exactly as it would a human-approved action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import structlog

from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.remediation import approval, executor
from kubepilot_orch.remediation.policy import RemediationPolicy
from kubepilot_orch.state import (
    BlastRadius,
    ExecutionRecord,
    InvestigationState,
    RemediationAction,
    RemediationPlan,
)

log = structlog.get_logger(__name__)

# Nothing is enabled by default — self-healing is strictly opt-in per pattern.
DEFAULT_ENABLED: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SelfHealPattern:
    name: str
    description: str
    matcher: Callable[[InvestigationState], RemediationAction | None]


def _imagepull_revert(state: InvestigationState) -> RemediationAction | None:
    rca = state.rca
    if rca is None or not state.service:
        return None
    if (rca.root_cause_category or "").lower() != "imagepullbackoff":
        return None
    return RemediationAction(
        tool="rollout_undo",
        target=f"deployment/{state.service}",
        namespace=state.namespace,
        reversibility="reversible",
        approval_tier="operator",
        rationale="Self-heal: revert the deploy with the bad image (ImagePullBackOff).",
        estimated_blast_radius=BlastRadius(pods_affected=None, summary="rolls back the workload"),
    )


def _crashloop_restart(state: InvestigationState) -> RemediationAction | None:
    rca = state.rca
    if rca is None or not state.service:
        return None
    cat = (rca.root_cause_category or "").lower()
    if cat not in ("crashloopbackoff", "transientcrash", "podrestart"):
        return None
    return RemediationAction(
        tool="restart_pod",
        target=f"deployment/{state.service}",
        namespace=state.namespace,
        reversibility="reversible",
        approval_tier="operator",
        rationale="Self-heal: restart the crash-looping pod (transient cause).",
        estimated_blast_radius=BlastRadius(pods_affected=1, traffic_percent=100.0),
    )


PATTERNS: dict[str, SelfHealPattern] = {
    p.name: p
    for p in (
        SelfHealPattern("imagepull_revert", "Revert a bad-image deploy", _imagepull_revert),
        SelfHealPattern("crashloop_restart", "Restart a crash-looping pod", _crashloop_restart),
    )
}


def select_action(
    state: InvestigationState, enabled: frozenset[str] | set[str]
) -> tuple[str, RemediationAction] | None:
    """First enabled pattern that matches → (pattern_name, auto-action). Else None."""
    for name, pattern in PATTERNS.items():
        if name not in enabled:
            continue
        action = pattern.matcher(state)
        if action is not None:
            return name, action
    return None


async def self_heal(
    state: InvestigationState,
    *,
    enabled: frozenset[str] | set[str],
    mcp_write: MCPClient,
    policy: RemediationPolicy | None,
    actor_role: str = "operator",
) -> list[ExecutionRecord]:
    """Autonomously execute a matched, enabled self-heal pattern — fully gated.

    The action still passes through the executor's policy + blast-radius + kill
    switch + audit pipeline; self-heal only skips the interactive HITL approval.
    """
    selected = select_action(state, enabled)
    if selected is None:
        return []
    name, action = selected
    log.info("self_heal_triggered", pattern=name, tool=action.tool, service=state.service)

    plan = RemediationPlan(actions=[action])
    # A non-interactive, system-recorded approval under the configured actor role,
    # so policy gates it exactly as a human-approved action would be.
    approvals = [
        approval.build_approval(
            action_index=0,
            decision="approved",
            approver_role=actor_role,
            approver="self-heal",
            reason=f"self-heal pattern {name}",
        )
    ]
    return await executor.execute_plan(plan, approvals, mcp_write=mcp_write, policy=policy)
