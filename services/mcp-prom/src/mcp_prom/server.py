"""Prometheus MCP server.

Same REST-flavored MCP shape as mcp-k8s (see docs/ARCHITECTURE.md §6.1):
  - /mcp/health   liveness
  - /mcp/tools    tool descriptors
  - /mcp/invoke   tool invocation
"""

from __future__ import annotations

import inspect
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mcp_prom import __version__
from mcp_prom.client import PrometheusError
from mcp_prom.tools import REGISTRY

log = structlog.get_logger(__name__)

app = FastAPI(title="mcp-prom", version=__version__)


class InvokeRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


class InvokeResponse(BaseModel):
    tool: str
    result: Any


@app.get("/mcp/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "server": "mcp-prom", "version": __version__}


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
    except PrometheusError as e:
        log.error("prom_api_error", tool=req.tool, status=e.status)
        raise HTTPException(
            status_code=502,
            detail={"upstream_status": e.status, "tool": req.tool},
        ) from e
    except TypeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return InvokeResponse(tool=req.tool, result=_to_jsonable(result))


def _accepted_kwargs(handler: Any, args: dict[str, Any]) -> dict[str, Any]:
    sig = inspect.signature(handler)
    return {name: args[name] for name in sig.parameters if name in args}


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value
