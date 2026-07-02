"""UI-editable settings endpoints (Phase: UI config).

    GET  /settings   → grouped effective settings + read-only infra facts + kill switch
    PUT  /settings   → admin-only: validate + persist overrides + rebuild the graph

Overrides are stored in the DB (survive restarts) and applied to the next
investigation by rebuilding the compiled graph. Secrets/infra stay env-managed
and are surfaced read-only. Every change is audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kubepilot_api import settings_catalog
from kubepilot_api.audit import emit_audit
from kubepilot_api.auth import Principal


class SettingsUpdate(BaseModel):
    overrides: dict[str, Any]


class KillSwitchBody(BaseModel):
    enabled: bool


def _orch_settings(request: Request) -> Any:
    orch = getattr(request.app.state, "orch_settings", None)
    if orch is None:
        from kubepilot_orch.config import load_settings as load_orch_settings

        orch = load_orch_settings()
        request.app.state.orch_settings = orch
    return orch


def make_settings_router(*, principal_dep) -> APIRouter:  # type: ignore[no-untyped-def]
    router = APIRouter(prefix="/settings")

    @router.get("")
    async def get_settings(
        request: Request, principal: Principal = Depends(principal_dep)
    ) -> dict[str, Any]:
        from kubepilot_orch.remediation import executor

        settings = request.app.state.settings
        orch = _orch_settings(request)
        store = request.app.state.settings_store
        overrides = await store.load()
        described = settings_catalog.describe(settings, orch, overrides)
        return {
            **described,
            "readonly": settings_catalog.readonly_facts(settings, orch),
            "kill_switch": executor.kill_switch_active(),
            "editable": bool(getattr(request.app.state, "rebuild_graph", None)),
        }

    @router.put("")
    async def put_settings(
        body: SettingsUpdate,
        request: Request,
        principal: Principal = Depends(principal_dep),
    ) -> dict[str, Any]:
        if not principal.is_admin():
            emit_audit(
                actor_role=principal.role,
                action="update_settings",
                resource="settings",
                decision="denied",
                reason="admin_required",
            )
            raise HTTPException(status_code=403, detail="only admin can change settings")

        try:
            settings_catalog.validate(body.overrides)
        except settings_catalog.SettingsValidationError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        store = request.app.state.settings_store
        await store.save(body.overrides)

        # Rebuild the graph so the change applies to the next investigation.
        rebuild = getattr(request.app.state, "rebuild_graph", None)
        rebuilt = False
        if rebuild is not None:
            try:
                await rebuild(body.overrides)
                rebuilt = True
            except Exception as e:  # keep the old graph on a bad rebuild
                emit_audit(
                    actor_role=principal.role,
                    action="update_settings",
                    resource="settings",
                    decision="error",
                    reason=str(e),
                )
                raise HTTPException(status_code=500, detail=f"rebuild failed: {e}") from e

        emit_audit(
            actor_role=principal.role,
            action="update_settings",
            resource="settings",
            decision="allowed",
            keys=sorted(body.overrides.keys()),
            rebuilt=rebuilt,
        )
        settings = request.app.state.settings
        orch = _orch_settings(request)
        return {
            "ok": True,
            "rebuilt": rebuilt,
            **settings_catalog.describe(settings, orch, body.overrides),
        }

    @router.post("/kill-switch")
    async def set_kill_switch(
        body: KillSwitchBody,
        principal: Principal = Depends(principal_dep),
    ) -> dict[str, Any]:
        from kubepilot_orch.remediation import executor

        if not principal.is_admin():
            raise HTTPException(status_code=403, detail="only admin can toggle the kill switch")
        executor.set_kill_switch(body.enabled)
        emit_audit(
            actor_role=principal.role,
            action="set_kill_switch",
            resource="remediation/kill-switch",
            decision="allowed",
            enabled=body.enabled,
        )
        return {"kill_switch": executor.kill_switch_active()}

    return router
