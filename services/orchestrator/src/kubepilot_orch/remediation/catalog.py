"""The write-action catalog — the orchestrator's view of the write surface (Phase 4).

Mirrors the curated allow-list served by ``mcp-k8s-write`` (kept in sync with its
``safety.py``). The remediation agent plans *only* within this catalog, and the
reversibility + approval tier are **code-filled from here**, never trusted from
the model. Because the catalog contains no destructive/irreversible actions,
nothing destructive can ever appear in a remediation plan.

At runtime the executor can additionally reconcile against the live write MCP's
``/mcp/tools`` (W7); this constant is the planning-time source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass

REVERSIBLE = "reversible"
PARTIAL = "partial"


@dataclass(frozen=True)
class WriteAction:
    name: str
    reversibility: str
    approval_tier: str  # minimum role to approve: "operator" | "admin"
    description: str


WRITE_CATALOG: dict[str, WriteAction] = {
    "rollout_undo": WriteAction(
        "rollout_undo", REVERSIBLE, "operator", "Roll a Deployment back to its previous revision."
    ),
    "rollout_restart": WriteAction(
        "rollout_restart", REVERSIBLE, "operator", "Restart a Deployment's pods (rolling)."
    ),
    "scale": WriteAction("scale", REVERSIBLE, "operator", "Set a Deployment's replica count."),
    "restart_pod": WriteAction(
        "restart_pod", REVERSIBLE, "operator", "Delete a Pod so its controller recreates it."
    ),
    "cordon": WriteAction("cordon", REVERSIBLE, "operator", "Mark a Node unschedulable."),
    "uncordon": WriteAction("uncordon", REVERSIBLE, "operator", "Mark a Node schedulable again."),
    "patch_image": WriteAction(
        "patch_image",
        REVERSIBLE,
        "operator",
        "Set a Deployment container image (e.g. revert a bad tag).",
    ),
    "edit_configmap": WriteAction("edit_configmap", PARTIAL, "admin", "Set keys in a ConfigMap."),
}


def catalog_prompt() -> str:
    """A compact catalog listing for the remediation prompt."""
    return "\n".join(
        f"- {a.name} ({a.reversibility}, approve:{a.approval_tier}): {a.description}"
        for a in sorted(WRITE_CATALOG.values(), key=lambda a: a.name)
    )
