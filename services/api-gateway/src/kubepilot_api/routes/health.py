"""Health + readiness endpoints — no auth required."""

from __future__ import annotations

from fastapi import APIRouter

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
