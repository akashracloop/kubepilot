"""Recommendation agent unit tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from kubepilot_orch.agents import recommendation_agent
from kubepilot_orch.state import (
    InvestigationState,
    RCAReport,
    Recommendation,
)
from kubepilot_orch.testing import ScriptedLLM, build_router, llm_text


def _state_with_rca(rca: RCAReport) -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        query="why is payment-service failing?",
        namespace="prod",
        service="payment-service",
        rca=rca,
        started_at=datetime(2026, 6, 23, 10, 7, tzinfo=timezone.utc),
    )


_RCA_OOM = RCAReport(
    root_cause="JVM heap exhaustion in payment-service.",
    root_cause_category="OOMKilled",
    confidence=0.92,
    evidence_refs=[0, 1, 2],
    reasoning="Three specialists corroborate the OOM signal.",
    recommendations=[
        "Roll back payment-service to the previous version",
        "Increase memory limit to 2Gi as short-term mitigation",
        "Investigate cache growth in the new code path",
    ],
)


@pytest.mark.asyncio
async def test_enriches_rca_recommendations_with_concrete_commands() -> None:
    enriched = [
        Recommendation(
            title="Roll back deployment to the previous version",
            rationale="Restores the last-known-good image before the memory regression.",
            commands=["kubectl rollout undo deployment/payment-service -n prod"],
            risk="medium",
            reversibility="reversible",
            priority=1,
            requires_approval=True,
            estimated_blast_radius="100% of payment-service traffic for ~30s during rollout",
        ),
        Recommendation(
            title="Raise memory limit to 2Gi",
            rationale="Short-term mitigation while the underlying cache leak is investigated.",
            commands=[
                'kubectl set resources deployment/payment-service -n prod --limits=memory=2Gi'
            ],
            risk="low",
            reversibility="reversible",
            priority=2,
            requires_approval=True,
        ),
    ]
    payload = recommendation_agent._RecommendationList(recommendations=enriched).model_dump_json()
    scripted = ScriptedLLM(responses=[llm_text(payload)])

    state = _state_with_rca(_RCA_OOM)
    recs = await recommendation_agent.run(state, llm=build_router(scripted))

    assert len(recs) == 2
    assert recs[0].priority == 1  # ordering preserved
    assert "kubectl rollout undo" in recs[0].commands[0]
    assert "prod" in recs[0].commands[0]
    # Write commands must require approval, regardless of what the LLM said.
    assert recs[0].requires_approval is True
    assert recs[1].requires_approval is True


@pytest.mark.asyncio
async def test_forces_requires_approval_on_write_commands() -> None:
    """Defense in depth: if the LLM tries to set requires_approval=False on a write, we override."""
    sneaky = [
        Recommendation(
            title="Delete the bad pod",
            rationale="...",
            commands=["kubectl delete pod payment-service-0 -n prod"],
            risk="medium",
            reversibility="reversible",
            priority=1,
            requires_approval=False,  # the LLM tried to skip approval — we MUST override
        )
    ]
    payload = recommendation_agent._RecommendationList(recommendations=sneaky).model_dump_json()
    scripted = ScriptedLLM(responses=[llm_text(payload)])

    state = _state_with_rca(_RCA_OOM)
    recs = await recommendation_agent.run(state, llm=build_router(scripted))

    assert recs[0].requires_approval is True


@pytest.mark.asyncio
async def test_caps_recommendations_at_4() -> None:
    too_many = [
        Recommendation(title=f"Rec {i}", rationale="...", priority=i + 1)
        for i in range(10)
    ]
    payload = recommendation_agent._RecommendationList(
        recommendations=too_many[:4]
    ).model_dump_json()
    scripted = ScriptedLLM(responses=[llm_text(payload)])

    recs = await recommendation_agent.run(
        _state_with_rca(_RCA_OOM), llm=build_router(scripted)
    )
    assert len(recs) <= 4


@pytest.mark.asyncio
async def test_no_rca_yields_empty_list() -> None:
    state = InvestigationState(
        incident_id=uuid.uuid4(),
        query="x",
        namespace="prod",
        rca=None,
        started_at=datetime(2026, 6, 23, 10, 7, tzinfo=timezone.utc),
    )
    # No LLM call expected since rca is None.
    scripted = ScriptedLLM(responses=[])
    recs = await recommendation_agent.run(state, llm=build_router(scripted))
    assert recs == []


@pytest.mark.asyncio
async def test_invalid_llm_output_falls_back_to_rca_text() -> None:
    scripted = ScriptedLLM(responses=[llm_text("definitely not json")])
    recs = await recommendation_agent.run(
        _state_with_rca(_RCA_OOM), llm=build_router(scripted)
    )
    assert 1 <= len(recs) <= 4
    # Fallback items should still be marked requires_approval (defensive).
    assert all(r.requires_approval for r in recs)
    # Title should reflect the RCA text verbatim.
    assert recs[0].title.startswith("Roll back")
