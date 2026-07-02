"""Kubernetes MCP server.

HTTP-flavored MCP semantics — agents discover tools at /mcp/tools and invoke
them at /mcp/invoke. Canonical MCP stdio transport is a Phase 2 add-on (see
docs/ARCHITECTURE.md §6.1); this REST shape is what the orchestrator calls.

All exposed tools are READ-ONLY. The RBAC ClusterRole bound to this pod's
ServiceAccount must contain only get/list/watch verbs — enforced by the
Helm chart and asserted by tests/test_rbac.py.
"""

from __future__ import annotations

import inspect
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from kubernetes.client.rest import ApiException
from pydantic import BaseModel

from mcp_k8s import __version__
from mcp_k8s.tools import REGISTRY

log = structlog.get_logger(__name__)

app = FastAPI(title="mcp-k8s", version=__version__)


class InvokeRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


class InvokeResponse(BaseModel):
    tool: str
    result: Any  # validated Pydantic model (list or single) serialized to JSON


@app.get("/mcp/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "server": "mcp-k8s", "version": __version__}


@app.get("/mcp/tools")
async def list_tools() -> dict[str, list[dict[str, Any]]]:
    return {"tools": REGISTRY.list_descriptors()}


@app.post("/mcp/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest) -> InvokeResponse:
    tool = REGISTRY.get(req.tool)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {req.tool}")

    accepted = _accepted_kwargs(tool.handler, req.arguments)
    try:
        result = await tool.handler(**accepted)
    except ApiException as e:
        log.error("k8s_api_error", tool=req.tool, status=e.status, reason=e.reason)
        raise HTTPException(
            status_code=502,
            detail={"k8s_status": e.status, "reason": e.reason, "tool": req.tool},
        ) from e
    except TypeError as e:
        # Argument shape mismatch — agent passed wrong args.
        raise HTTPException(status_code=400, detail=str(e)) from e

    return InvokeResponse(tool=req.tool, result=_to_jsonable(result))


def _accepted_kwargs(handler: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Pass only kwargs the handler's signature actually accepts. Defensive — agents
    sometimes pass extra metadata fields. Mismatch on *required* args still raises."""
    sig = inspect.signature(handler)
    accepted: dict[str, Any] = {}
    for name in sig.parameters:
        if name in args:
            accepted[name] = args[name]
    return accepted


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value
