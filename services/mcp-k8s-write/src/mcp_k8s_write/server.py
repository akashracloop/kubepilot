"""Write MCP server - same REST contract as the read servers, but gated.

  - /mcp/health   liveness (+ whether apply is enabled)
  - /mcp/tools    the curated write-tool descriptors
  - /mcp/invoke   returns a **dry-run** WriteResult (Phase 4 W1 applies nothing)

Hard off switch: real application is gated behind ``KUBEPILOT_WRITE_APPLY_ENABLED``
(default false). In W1 the apply path is not implemented, so **every** invoke is a
dry run regardless of the flag or the request - the server cannot mutate a cluster.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mcp_k8s_write import __version__
from mcp_k8s_write.models import WriteResult
from mcp_k8s_write.safety import WRITE_TOOLS

log = structlog.get_logger(__name__)

app = FastAPI(title="mcp-k8s-write", version=__version__)


def _apply_enabled() -> bool:
    return os.getenv("KUBEPILOT_WRITE_APPLY_ENABLED", "false").lower() in ("1", "true", "yes")


class InvokeRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}
    dry_run: bool = True  # honored, but W1 forces dry-run regardless (see below)


class InvokeResponse(BaseModel):
    tool: str
    result: WriteResult


@app.get("/mcp/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "server": "mcp-k8s-write",
        "version": __version__,
        # Surfaced so operators can confirm the write path is inert.
        "apply_enabled": _apply_enabled(),
        "mode": "dry-run-only",
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

    warnings: list[str] = []
    # Phase 4 W1: dry-run ONLY. Even an explicit apply request is refused here -
    # real execution arrives with the policy/approval/executor pipeline (W7).
    if not req.dry_run:
        warnings.append("apply requested but this server is dry-run-only (W1); nothing was applied")
    if not _apply_enabled():
        warnings.append("KUBEPILOT_WRITE_APPLY_ENABLED is false")

    result = WriteResult(
        tool=spec.name,
        target=str(target),
        namespace=namespace,
        reversibility=spec.reversibility,
        approval_tier=spec.approval_tier,
        dry_run=True,
        applied=False,
        preview=_preview(spec.name, namespace, str(target), args),
        note="dry-run only (Phase 4 W1) - no cluster mutation performed",
        warnings=warnings,
    )
    log.info("write_dry_run", tool=spec.name, namespace=namespace, target=target)
    return InvokeResponse(tool=spec.name, result=result)


def _preview(tool: str, namespace: str | None, target: str, args: dict[str, Any]) -> str:
    """Human-readable description of the would-be change (no cluster call in W1)."""
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
