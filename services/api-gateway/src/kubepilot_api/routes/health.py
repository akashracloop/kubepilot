"""Health + readiness + calibration endpoints — no auth required."""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kubepilot_api import __version__

log = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
async def ready(request: Request) -> Any:
    """Readiness: is this pod able to serve investigations?

    The **database** is the gateway's own critical dependency (it persists and
    serves every investigation) — a DB failure returns 503 so the pod is pulled
    from service. The **kubernetes MCP** and **LLM credentials** are checked and
    reported too, but are non-fatal: their outages degrade investigations rather
    than the API itself, and gating readiness on them would flap the pod out of
    service on a transient blip. Operators watch the reported component states.
    """
    settings = request.app.state.settings
    repo = request.app.state.repo
    checks: dict[str, str] = {}

    # Database (fatal): a trivial query exercises the connection pool.
    try:
        await repo.list(limit=1)
        checks["database"] = "ok"
        db_ok = True
    except Exception as e:  # pool exhausted / DB unreachable
        log.warning("ready_db_check_failed", error=str(e))
        checks["database"] = f"error: {e}"
        db_ok = False

    checks["mcp_kubernetes"] = await _probe_mcp(settings.mcp.k8s)
    checks["llm"] = "ok" if _llm_configured() else "no provider credentials configured"

    body = {"status": "ready" if db_ok else "not_ready", "checks": checks}
    if not db_ok:
        return JSONResponse(status_code=503, content=body)
    return body


async def _probe_mcp(base_url: str) -> str:
    """Best-effort /mcp/health probe with a short timeout (never raises)."""
    if not base_url:
        return "not_configured"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(base_url.rstrip("/") + "/mcp/health")
        return "ok" if resp.status_code == 200 else f"unhealthy: HTTP {resp.status_code}"
    except Exception as e:
        return f"unreachable: {e}"


def _llm_configured() -> bool:
    """Whether some LLM provider is usable (a key, or a keyless local/IAM provider)."""
    try:
        from kubepilot_orch.config import load_settings as load_orch_settings

        llm = load_orch_settings().llm
    except Exception:
        return False
    # Local/IAM providers need no API key.
    if llm.default_provider in ("ollama", "vllm", "bedrock"):
        return True
    return bool(llm.anthropic_api_key or llm.openai_api_key or llm.azure_api_key)


@router.get("/calibration")
async def calibration(request: Request) -> dict[str, Any]:
    """Confidence-calibration map for the AgentOps plot (Phase 3).

    Returns the loaded isotonic calibrator's raw→calibrated points, or an empty
    curve + ``fitted: false`` when no calibrator is configured. Read-only, no auth.
    """
    from kubepilot_api.main import _build_calibrator

    settings = request.app.state.settings
    calibrator = _build_calibrator(settings)
    if calibrator is None or not calibrator.is_fitted:
        return {"fitted": False, "curve": []}
    return {"fitted": True, "curve": calibrator.curve()}
