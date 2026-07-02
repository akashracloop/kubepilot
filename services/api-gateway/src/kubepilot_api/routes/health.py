"""Health + readiness + calibration endpoints — no auth required."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from kubepilot_api import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
async def ready() -> dict[str, str]:
    # In W11 this will check MCP servers, DB pool, LLM provider creds.
    # For now: process is up == ready.
    return {"status": "ok"}


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
