"""HITL approval endpoints for remediation plans (Phase 4 W5).

A pending remediation plan (produced when remediation is enabled) waits for an
explicit human decision. These endpoints record that decision into the
investigation's persisted state, gated by **approver RBAC** (an approver's role
must be at least the action's required tier) and **audited** (approve/reject both
emit audit events). The graph resume + actual execution is wired in W7.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from kubepilot_orch.remediation import approval
from kubepilot_orch.state import InvestigationState
from pydantic import BaseModel, Field

from kubepilot_api.audit import emit_audit
from kubepilot_api.auth import Principal
from kubepilot_api.repository import InvestigationRepository


class DecisionRequest(BaseModel):
    action_index: int = Field(ge=0)
    reason: str | None = None


def _repo(request: Request) -> InvestigationRepository:
    return request.app.state.repo  # type: ignore[no-any-return]


def make_approval_router(*, principal_dep) -> APIRouter:  # type: ignore[no-untyped-def]
    router = APIRouter(prefix="/investigations")

    async def _load(
        request: Request, incident_id: UUID, principal: Principal
    ) -> InvestigationState:
        record = await _repo(request).get(incident_id)
        if record is None or not principal.allows_namespace(record.namespace):
            raise HTTPException(status_code=404, detail=f"Investigation {incident_id} not found")
        return InvestigationState.model_validate(record.state_json)

    @router.get("/{incident_id}/approval")
    async def get_approval(
        incident_id: UUID,
        request: Request,
        principal: Principal = Depends(principal_dep),
    ) -> dict[str, Any]:
        state = await _load(request, incident_id, principal)
        plan = state.remediation_plan
        if plan is None:
            return {"status": "no_plan", "actions": []}
        status = approval.plan_status(plan, state.approvals, generated_at=plan.generated_at)
        return {
            "status": status,
            "actions": [
                {
                    "index": i,
                    "tool": a.tool,
                    "target": a.target,
                    "namespace": a.namespace,
                    "reversibility": a.reversibility,
                    "approval_tier": a.approval_tier,
                    "rationale": a.rationale,
                    "blast_radius": a.estimated_blast_radius.model_dump()
                    if a.estimated_blast_radius
                    else None,
                    "dry_run_preview": a.dry_run_preview,
                }
                for i, a in enumerate(plan.actions)
            ],
        }

    async def _decide(
        request: Request,
        incident_id: UUID,
        principal: Principal,
        body: DecisionRequest,
        decision: str,
    ) -> dict[str, Any]:
        state = await _load(request, incident_id, principal)
        plan = state.remediation_plan
        if plan is None or body.action_index >= len(plan.actions):
            raise HTTPException(status_code=404, detail="No such remediation action")
        action = plan.actions[body.action_index]

        # Approver RBAC: role must be at least the action's required tier.
        if not approval.authorize(action, principal.role):
            emit_audit(
                actor_role=principal.role,
                action=f"{decision}_remediation",
                resource=f"investigation/{incident_id}/action/{body.action_index}",
                namespace=action.namespace,
                decision="denied",
                reason="insufficient_approval_tier",
            )
            raise HTTPException(
                status_code=403,
                detail=f"role {principal.role!r} cannot {decision} a {action.approval_tier}-tier action",
            )

        state.approvals.append(
            approval.build_approval(
                action_index=body.action_index,
                decision=decision,
                approver_role=principal.role,
                reason=body.reason,
            )
        )
        state.remediation_outcome = approval.plan_status(
            plan, state.approvals, generated_at=plan.generated_at
        )
        await _repo(request).update_state(incident_id, state)

        emit_audit(
            actor_role=principal.role,
            action=f"{decision}_remediation",
            resource=f"investigation/{incident_id}/action/{body.action_index}",
            namespace=action.namespace,
            decision="allowed",
            tool=action.tool,
            plan_status=state.remediation_outcome,
        )

        # Once every action has a terminal decision, resume the paused graph so the
        # execute node runs (approved → writes; rejected/expired → resolve without
        # writes → finalize). While the plan is still partially decided we just
        # accumulate the decision and keep waiting.
        if state.remediation_outcome in ("approved", "rejected", "expired"):
            orchestrator = getattr(request.app.state, "orchestrator", None)
            if orchestrator is not None:
                orchestrator.start_resume(incident_id)

        return {"status": state.remediation_outcome, "action_index": body.action_index}

    @router.post("/{incident_id}/approve")
    async def approve(
        incident_id: UUID,
        body: DecisionRequest,
        request: Request,
        principal: Principal = Depends(principal_dep),
    ) -> dict[str, Any]:
        if not principal.can_investigate():
            raise HTTPException(status_code=403, detail="viewer role cannot approve remediations")
        return await _decide(request, incident_id, principal, body, "approved")

    @router.post("/{incident_id}/reject")
    async def reject(
        incident_id: UUID,
        body: DecisionRequest,
        request: Request,
        principal: Principal = Depends(principal_dep),
    ) -> dict[str, Any]:
        if not principal.can_investigate():
            raise HTTPException(status_code=403, detail="viewer role cannot reject remediations")
        return await _decide(request, incident_id, principal, body, "rejected")

    return router


class KillSwitchRequest(BaseModel):
    enabled: bool


def make_kill_switch_router(*, principal_dep) -> APIRouter:  # type: ignore[no-untyped-def]
    """Global remediation kill switch — halts all execution immediately (admin-only)."""
    router = APIRouter(prefix="/remediation")

    @router.get("/kill-switch")
    async def get_kill_switch(principal: Principal = Depends(principal_dep)) -> dict[str, Any]:
        from kubepilot_orch.remediation import executor

        return {"enabled": executor.kill_switch_active()}

    @router.post("/kill-switch")
    async def set_kill_switch(
        body: KillSwitchRequest, principal: Principal = Depends(principal_dep)
    ) -> dict[str, Any]:
        from kubepilot_orch.remediation import executor

        if not principal.is_admin():
            emit_audit(
                actor_role=principal.role,
                action="set_kill_switch",
                resource="remediation/kill-switch",
                decision="denied",
                reason="admin_required",
            )
            raise HTTPException(status_code=403, detail="only admin can toggle the kill switch")
        executor.set_kill_switch(body.enabled)
        emit_audit(
            actor_role=principal.role,
            action="set_kill_switch",
            resource="remediation/kill-switch",
            decision="allowed",
            enabled=body.enabled,
        )
        return {"enabled": executor.kill_switch_active()}

    return router
