"""Thin cluster-fact fetch for the remediation path (Phase 4).

Reads (via the read-only ``mcp-k8s``) the small set of live facts the write path
needs but must not guess:

  - ``gather_blast_facts`` — current pods/replicas for the target, so
    ``blast_radius.estimate`` produces a real (not zero) impact before approval.
  - ``capture_pre_state`` — the target's current replicas / image, so an
    auto-rollback has an inverse to apply (``rollback.inverse_action``).

All fetches fail soft: a read error yields conservative empties/None rather than
blocking the (still HITL-gated) plan. Everything here is READ-only — the writes
stay behind the executor + mcp-k8s-write gate.
"""

from __future__ import annotations

from typing import Any

import structlog

from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.remediation import blast_radius
from kubepilot_orch.state import BlastRadius, RemediationAction, ServiceKnowledge

log = structlog.get_logger(__name__)


def _target_name(target: str) -> str:
    """'deployment/checkout' -> 'checkout'; 'checkout' -> 'checkout'."""
    return target.split("/", 1)[1] if "/" in target else target


async def _find_deployment(action: RemediationAction, mcp_k8s: MCPClient) -> dict[str, Any] | None:
    """The DeploymentSummary dict for the action's target, or None."""
    name = _target_name(action.target)
    try:
        deployments = await mcp_k8s.invoke("get_deployments", {"namespace": action.namespace})
    except Exception as e:  # read failures must not block the (gated) plan
        log.warning("blast_facts_fetch_failed", target=action.target, error=str(e))
        return None
    for d in deployments or []:
        if isinstance(d, dict) and d.get("name") == name:
            return d
    return None


async def estimate_blast_radius(
    action: RemediationAction,
    mcp_k8s: MCPClient,
    knowledge: list[ServiceKnowledge] | None = None,
) -> BlastRadius:
    """Estimate a conservative blast radius from live cluster facts."""
    dep = await _find_deployment(action, mcp_k8s)
    replicas = int(dep.get("replicas", 0)) if dep else 0
    ready = int(dep.get("ready_replicas", 0)) if dep else 0
    current_pods = ready or replicas
    return blast_radius.estimate(
        action,
        current_pods=current_pods,
        current_replicas=replicas or None,
        knowledge=knowledge or [],
    )


async def capture_pre_state(action: RemediationAction, mcp_k8s: MCPClient) -> dict[str, Any] | None:
    """Snapshot the target's reversible state so a rollback has an inverse.

    ``scale`` → ``{"replicas": N}``; ``patch_image`` → ``{"image": X, "container": C}``.
    Other tools have no captured pre-state (their inverse is self-contained, e.g.
    cordon↔uncordon, or non-invertible here). Returns None when unavailable.
    """
    if action.tool == "scale":
        dep = await _find_deployment(action, mcp_k8s)
        if dep is not None:
            return {"replicas": int(dep.get("replicas", 0))}
        return None
    if action.tool == "patch_image":
        container = action.arguments.get("container")
        image = await _current_image(action, mcp_k8s, container)
        if image is not None:
            return {"image": image, "container": container}
        return None
    return None


async def _current_image(
    action: RemediationAction, mcp_k8s: MCPClient, container: str | None
) -> str | None:
    """Best-effort current image for the target's container (via describe_pod)."""
    name = _target_name(action.target)
    try:
        pods = await mcp_k8s.invoke(
            "list_pods", {"namespace": action.namespace, "label_selector": f"app={name}"}
        )
    except Exception as e:
        log.warning("pre_state_image_fetch_failed", target=action.target, error=str(e))
        return None
    for p in pods or []:
        if not isinstance(p, dict):
            continue
        for c in p.get("containers", []) or []:
            if container is None or c.get("name") == container:
                image = c.get("image")
                if image:
                    return str(image)
    return None
