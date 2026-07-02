"""The write allow-list - the finite set of mutations KubePilot may ever make.

Each entry declares its **reversibility**, the minimum **approval tier**, and the
exact Kubernetes **verbs x resources** it needs. The last is the contract the
Helm ClusterRole is generated from and that ``test_rbac_write.py`` asserts against
- so the write ServiceAccount is granted nothing beyond what these tools require.

Deliberately absent: anything destructive/irreversible-by-default (delete
pvc/pv/namespace/secret, `--force --grace-period=0`, drain). Those are blocked by
the Phase 3 recommendation guardrail and are NOT part of the write surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Reversibility tiers (mirrors state.RemediationAction.reversibility).
REVERSIBLE = "reversible"
PARTIAL = "partial"

# Approval tiers (map to RBAC v2 roles that may approve).
TIER_OPERATOR = "operator"
TIER_ADMIN = "admin"


@dataclass(frozen=True)
class WriteToolSpec:
    """Static metadata + RBAC footprint for one write tool."""

    name: str
    description: str
    reversibility: str
    approval_tier: str
    # Kubernetes RBAC footprint this tool needs, as {apiGroup: {resources}} → verbs.
    rbac: dict[str, tuple[str, ...]]  # "<apiGroup>/<resource>" -> verbs
    parameters: dict[str, object] = field(default_factory=dict)


def _params(**props: object) -> dict[str, object]:
    return {"type": "object", "properties": props, "required": ["namespace", "target"]}


_NS_TARGET = {"namespace": {"type": "string"}, "target": {"type": "string"}}


# The complete write surface. Reversible-leaning by design; nothing here can
# cause irreversible data loss.
WRITE_TOOLS: dict[str, WriteToolSpec] = {
    "rollout_undo": WriteToolSpec(
        name="rollout_undo",
        description="Roll a Deployment back to its previous revision (reversible).",
        reversibility=REVERSIBLE,
        approval_tier=TIER_OPERATOR,
        rbac={"apps/deployments": ("get", "patch", "update"), "apps/replicasets": ("get", "list")},
        parameters=_params(**_NS_TARGET, to_revision={"type": "integer"}),
    ),
    "rollout_restart": WriteToolSpec(
        name="rollout_restart",
        description="Restart a Deployment's pods (rolling), reversible.",
        reversibility=REVERSIBLE,
        approval_tier=TIER_OPERATOR,
        rbac={"apps/deployments": ("get", "patch")},
        parameters=_params(**_NS_TARGET),
    ),
    "scale": WriteToolSpec(
        name="scale",
        description="Set a Deployment's replica count (reversible; blast-radius capped).",
        reversibility=REVERSIBLE,
        approval_tier=TIER_OPERATOR,
        rbac={"apps/deployments": ("get", "patch"), "apps/deployments/scale": ("get", "update")},
        parameters=_params(**_NS_TARGET, replicas={"type": "integer"}),
    ),
    "restart_pod": WriteToolSpec(
        name="restart_pod",
        description="Delete a single Pod so its controller recreates it (reversible).",
        reversibility=REVERSIBLE,
        approval_tier=TIER_OPERATOR,
        rbac={"/pods": ("get", "delete")},
        parameters=_params(**_NS_TARGET),
    ),
    "cordon": WriteToolSpec(
        name="cordon",
        description="Mark a Node unschedulable (reversible via uncordon).",
        reversibility=REVERSIBLE,
        approval_tier=TIER_OPERATOR,
        rbac={"/nodes": ("get", "patch")},
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    ),
    "uncordon": WriteToolSpec(
        name="uncordon",
        description="Mark a Node schedulable again (reversible).",
        reversibility=REVERSIBLE,
        approval_tier=TIER_OPERATOR,
        rbac={"/nodes": ("get", "patch")},
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    ),
    "patch_image": WriteToolSpec(
        name="patch_image",
        description="Set a Deployment container image (e.g. revert a bad tag). Reversible.",
        reversibility=REVERSIBLE,
        approval_tier=TIER_OPERATOR,
        rbac={"apps/deployments": ("get", "patch")},
        parameters=_params(**_NS_TARGET, container={"type": "string"}, image={"type": "string"}),
    ),
    "edit_configmap": WriteToolSpec(
        name="edit_configmap",
        description="Set keys in a ConfigMap (partial reversibility - admin approval).",
        reversibility=PARTIAL,
        approval_tier=TIER_ADMIN,
        rbac={"/configmaps": ("get", "update", "patch")},
        parameters=_params(**_NS_TARGET, data={"type": "object"}),
    ),
}


def tool_names() -> list[str]:
    return sorted(WRITE_TOOLS)


def required_rbac() -> dict[str, set[str]]:
    """Aggregate the full RBAC footprint across all write tools → {group/resource: verbs}."""
    out: dict[str, set[str]] = {}
    for spec in WRITE_TOOLS.values():
        for key, verbs in spec.rbac.items():
            out.setdefault(key, set()).update(verbs)
    return out
