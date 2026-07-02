"""Blast-radius estimation (Phase 4 W3).

Before any remediation is approved, estimate its impact — **conservatively**
(over-estimate). The estimate feeds the policy caps (W2) and the approval UI (W6).

Inputs are plain cluster facts (current pods/replicas for the target, pods on a
node) + the cluster knowledge graph's **dependents** (so "restart payments-db"
surfaces that checkout rides on it). Pure and unit-testable; the thin fetch that
gathers the facts from ``mcp-k8s`` is wired into the executor path (W7).
"""

from __future__ import annotations

from kubepilot_orch.state import BlastRadius, RemediationAction, ServiceKnowledge

# Actions that affect the whole target workload (all its pods / 100% traffic).
_WORKLOAD_WIDE = {"rollout_undo", "rollout_restart", "patch_image", "edit_configmap"}


def dependents_for(target: str, knowledge: list[ServiceKnowledge]) -> list[str]:
    """Dependents of the action's target service, from the knowledge graph."""
    svc = _service_of(target)
    for fact in knowledge:
        if fact.service == svc:
            return list(fact.dependents)
    return []


def estimate(
    action: RemediationAction,
    *,
    current_pods: int,
    current_replicas: int | None = None,
    node_pods: int | None = None,
    knowledge: list[ServiceKnowledge] | None = None,
) -> BlastRadius:
    """Estimate a conservative blast radius for one remediation action."""
    knowledge = knowledge or []
    dependents = dependents_for(action.target, knowledge)
    pods = 0
    traffic = 0.0

    if action.tool == "scale":
        target_replicas = int(action.arguments.get("replicas", current_pods) or 0)
        # Pods that change (up or down); conservatively at least 1 if it changes.
        base = current_replicas if current_replicas is not None else current_pods
        pods = abs(target_replicas - base)
        # Scaling down toward 0 approaches full traffic impact; else proportional.
        if base > 0:
            traffic = min(100.0, 100.0 * pods / base)
    elif action.tool == "restart_pod":
        pods = 1
        traffic = min(100.0, 100.0 / current_pods) if current_pods else 100.0
    elif action.tool in ("cordon", "uncordon"):
        pods = node_pods if node_pods is not None else current_pods
        traffic = 100.0  # a node op can shift any pod scheduled there — be conservative
    elif action.tool in _WORKLOAD_WIDE:
        pods = current_pods
        traffic = 100.0  # a rolling change touches the whole workload
    else:
        pods = current_pods
        traffic = 100.0

    summary = _summary(action, pods, traffic, dependents)
    return BlastRadius(
        pods_affected=pods,
        traffic_percent=round(traffic, 1),
        dependents=dependents,
        summary=summary,
    )


def _service_of(target: str) -> str:
    """'deployment/checkout-service' -> 'checkout-service'; 'checkout' -> 'checkout'."""
    return target.split("/", 1)[1] if "/" in target else target


def _summary(action: RemediationAction, pods: int, traffic: float, dependents: list[str]) -> str:
    dep = f"; dependents at risk: {', '.join(dependents)}" if dependents else ""
    return (
        f"{action.tool} on {action.target} in {action.namespace}: "
        f"~{pods} pod(s), ~{traffic:.0f}% of service traffic{dep}"
    )
