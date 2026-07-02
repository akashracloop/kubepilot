"""Write MCP server - same REST contract as the read servers, but gated.

  - /mcp/health   liveness (+ whether apply is enabled)
  - /mcp/tools    the curated write-tool descriptors
  - /mcp/invoke   applies a curated mutation (or a dry-run preview)

Hard off switch: real application is gated behind ``KUBEPILOT_WRITE_APPLY_ENABLED``
(default false). With it OFF, **every** invoke is a dry run regardless of the
request — the server cannot mutate a cluster. With it ON, an invoke that isn't an
explicit ``dry_run`` preview performs the real mutation via the least-privilege
kubernetes client. Callers wanting a preview even when apply is enabled pass
``dry_run: true`` (server-side ``dryRun=All``).
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mcp_k8s_write import __version__
from mcp_k8s_write.apply import ApplyError, apply_tool
from mcp_k8s_write.models import WriteResult
from mcp_k8s_write.safety import WRITE_TOOLS

log = structlog.get_logger(__name__)

app = FastAPI(title="mcp-k8s-write", version=__version__)


def _apply_enabled() -> bool:
    return os.getenv("KUBEPILOT_WRITE_APPLY_ENABLED", "false").lower() in ("1", "true", "yes")


class InvokeRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}
    # An approved action wants a real apply; the server's apply flag is the hard
    # gate. Set true to force a dry-run preview even when apply is enabled.
    dry_run: bool = False


class InvokeResponse(BaseModel):
    tool: str
    result: WriteResult


@app.get("/mcp/health")
async def health() -> dict[str, Any]:
    enabled = _apply_enabled()
    return {
        "status": "ok",
        "server": "mcp-k8s-write",
        "version": __version__,
        # Surfaced so operators can confirm the write path's posture.
        "apply_enabled": enabled,
        "mode": "apply-enabled" if enabled else "dry-run-only",
    }


@app.get("/mcp/tools")
async def list_tools() -> dict[str, list[dict[str, Any]]]:
    return {
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
                "reversibility": spec.reversibility,
                "approval_tier": spec.approval_tier,
            }
            for spec in sorted(WRITE_TOOLS.values(), key=lambda s: s.name)
        ]
    }


@app.post("/mcp/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest) -> InvokeResponse:
    spec = WRITE_TOOLS.get(req.tool)
    if spec is None:
        # Fail closed: an unknown tool is never a no-op success.
        raise HTTPException(status_code=404, detail=f"Unknown or forbidden write tool: {req.tool}")

    args = req.arguments or {}
    namespace = args.get("namespace")
    target = args.get("target") or args.get("node") or "<unspecified>"
    preview = _preview(spec.name, namespace, str(target), args)

    apply_enabled = _apply_enabled()
    # The apply flag is the hard gate. Perform a real mutation only when it is on
    # AND the caller did not force a preview. Anything else is a dry run.
    do_apply = apply_enabled and not req.dry_run

    warnings: list[str] = []
    if not apply_enabled:
        warnings.append("KUBEPILOT_WRITE_APPLY_ENABLED is false; dry-run-only, nothing applied")

    if not do_apply:
        result = WriteResult(
            tool=spec.name,
            target=str(target),
            namespace=namespace,
            reversibility=spec.reversibility,
            approval_tier=spec.approval_tier,
            dry_run=True,
            applied=False,
            preview=preview,
            note="dry-run — no cluster mutation performed",
            warnings=warnings,
        )
        log.info("write_dry_run", tool=spec.name, namespace=namespace, target=target)
        return InvokeResponse(tool=spec.name, result=result)

    # Gate open + real apply requested → mutate the cluster via the curated tool.
    try:
        outcome = await apply_tool(spec.name, namespace, str(target), args, dry_run=False)
    except ApplyError as e:
        # Never a silent success: surface the failure so the executor records it.
        log.warning("write_apply_failed", tool=spec.name, target=target, error=str(e))
        raise HTTPException(status_code=502, detail=str(e)) from e

    result = WriteResult(
        tool=spec.name,
        target=str(target),
        namespace=namespace,
        reversibility=spec.reversibility,
        approval_tier=spec.approval_tier,
        dry_run=False,
        applied=bool(outcome.get("applied")),
        preview=preview,
        note=str(outcome.get("note") or "applied"),
        warnings=warnings,
    )
    log.info("write_applied", tool=spec.name, namespace=namespace, target=target)
    return InvokeResponse(tool=spec.name, result=result)


def _preview(tool: str, namespace: str | None, target: str, args: dict[str, Any]) -> str:
    """Human-readable description of the would-be change (kubectl-equivalent)."""
    ns = f" -n {namespace}" if namespace else ""
    match tool:
        case "rollout_undo":
            rev = f" --to-revision={args['to_revision']}" if args.get("to_revision") else ""
            return f"kubectl rollout undo {target}{ns}{rev}  (dry run)"
        case "rollout_restart":
            return f"kubectl rollout restart {target}{ns}  (dry run)"
        case "scale":
            return f"kubectl scale {target}{ns} --replicas={args.get('replicas')}  (dry run)"
        case "restart_pod":
            return f"kubectl delete pod {target}{ns}  (controller recreates it; dry run)"
        case "cordon":
            return f"kubectl cordon {target}  (dry run)"
        case "uncordon":
            return f"kubectl uncordon {target}  (dry run)"
        case "patch_image":
            return (
                f"kubectl set image {target}{ns} "
                f"{args.get('container', '<container>')}={args.get('image', '<image>')}  (dry run)"
            )
        case "edit_configmap":
            keys = ", ".join((args.get("data") or {}).keys()) or "<keys>"
            return f"patch configmap {target}{ns} keys: {keys}  (dry run)"
        case _:
            return f"{tool} {target}{ns}  (dry run)"
