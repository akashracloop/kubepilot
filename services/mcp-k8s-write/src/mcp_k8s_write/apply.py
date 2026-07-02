"""Real Kubernetes mutations for the curated write tools (Phase 4).

The server calls this ONLY when ``KUBEPILOT_WRITE_APPLY_ENABLED`` is set and the
request is not an explicit preview. Every mutation supports server-side dry-run
(``dryRun=All``) so a "preview" is a genuine apiserver validation, not a guess.
The sync ``kubernetes`` client runs in a worker thread so the async server stays
responsive.

Each tool maps to exactly the API verbs declared for it in ``safety.WRITE_TOOLS``
— nothing here reaches beyond the least-privilege ClusterRole.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from kubernetes.client.rest import ApiException

from mcp_k8s_write.client import get_apps_v1, get_core_v1

log = structlog.get_logger(__name__)

_RESTART_ANNOTATION = "kubepilot.ai/restartedAt"


class ApplyError(Exception):
    """A mutation could not be performed — surfaced to the caller as a failure
    (never a silent success)."""


def _name(target: str) -> str:
    """'deployment/checkout' -> 'checkout'; 'checkout' -> 'checkout'."""
    return target.split("/", 1)[1] if "/" in target else target


async def apply_tool(
    tool: str, namespace: str | None, target: str, args: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    """Perform (or server-side dry-run) one write tool. Returns {applied, note}."""
    try:
        return await asyncio.to_thread(_apply_sync, tool, namespace, target, args, dry_run)
    except ApiException as e:
        raise ApplyError(f"{tool} on {target}: apiserver {e.status} {e.reason}") from e


def _apply_sync(
    tool: str, namespace: str | None, target: str, args: dict[str, Any], dry_run: bool
) -> dict[str, Any]:
    dr = ["All"] if dry_run else None
    name = _name(target)

    if tool == "scale":
        replicas = int(args["replicas"])
        get_apps_v1().patch_namespaced_deployment_scale(
            name, namespace, {"spec": {"replicas": replicas}}, dry_run=dr
        )
        return _ok(dry_run, f"scaled {name} to {replicas} replicas")

    if tool == "rollout_restart":
        ts = datetime.now(UTC).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {_RESTART_ANNOTATION: ts}}}}}
        get_apps_v1().patch_namespaced_deployment(name, namespace, body, dry_run=dr)
        return _ok(dry_run, f"restarted {name}")

    if tool == "patch_image":
        container = args["container"]
        image = args["image"]
        body = {
            "spec": {"template": {"spec": {"containers": [{"name": container, "image": image}]}}}
        }
        get_apps_v1().patch_namespaced_deployment(name, namespace, body, dry_run=dr)
        return _ok(dry_run, f"set {name}/{container} image to {image}")

    if tool == "restart_pod":
        get_core_v1().delete_namespaced_pod(name, namespace, dry_run=dr)
        return _ok(dry_run, f"deleted pod {name} (controller recreates it)")

    if tool in ("cordon", "uncordon"):
        unschedulable = tool == "cordon"
        get_core_v1().patch_node(name, {"spec": {"unschedulable": unschedulable}}, dry_run=dr)
        return _ok(dry_run, f"{tool}ed node {name}")

    if tool == "edit_configmap":
        data = args.get("data") or {}
        get_core_v1().patch_namespaced_config_map(name, namespace, {"data": data}, dry_run=dr)
        keys = ", ".join(data.keys()) or "<none>"
        return _ok(dry_run, f"patched configmap {name} keys: {keys}")

    if tool == "rollout_undo":
        return _rollout_undo(name, namespace, args, dr, dry_run)

    raise ApplyError(f"no apply implementation for tool {tool!r}")


def _rollout_undo(
    name: str, namespace: str | None, args: dict[str, Any], dr: list[str] | None, dry_run: bool
) -> dict[str, Any]:
    """Roll a Deployment back to a prior revision (kubectl rollout undo).

    Finds the ReplicaSet for the target revision (the one below current, or an
    explicit ``to_revision``) and patches the Deployment's pod template to match.
    """
    apps = get_apps_v1()
    dep = apps.read_namespaced_deployment(name, namespace)
    annotations = dep.metadata.annotations or {}
    current = int(annotations.get("deployment.kubernetes.io/revision", "0"))

    match_labels = (dep.spec.selector.match_labels or {}) if dep.spec.selector else {}
    selector = ",".join(f"{k}={v}" for k, v in match_labels.items())
    rs_list = apps.list_namespaced_replica_set(namespace, label_selector=selector or None)

    revisions: dict[int, Any] = {}
    for rs in rs_list.items:
        owners = rs.metadata.owner_references or []
        if not any(o.uid == dep.metadata.uid for o in owners):
            continue
        rs_ann = rs.metadata.annotations or {}
        rev = rs_ann.get("deployment.kubernetes.io/revision")
        if rev is not None:
            revisions[int(rev)] = rs

    want = (
        int(args["to_revision"])
        if args.get("to_revision")
        else max((r for r in revisions if r < current), default=-1)
    )
    if want not in revisions:
        raise ApplyError(
            f"no revision {want if args.get('to_revision') else 'below current'} to roll "
            f"back to (current={current}, available={sorted(revisions)})"
        )

    template = revisions[want].spec.template
    # Drop the controller-managed pod-template-hash so the Deployment recomputes it.
    if template.metadata and template.metadata.labels:
        template.metadata.labels.pop("pod-template-hash", None)
    body = {"spec": {"template": apps.api_client.sanitize_for_serialization(template)}}
    apps.patch_namespaced_deployment(name, namespace, body, dry_run=dr)
    return _ok(dry_run, f"rolled back {name} to revision {want}")


def _ok(dry_run: bool, note: str) -> dict[str, Any]:
    return {"applied": not dry_run, "note": note}
