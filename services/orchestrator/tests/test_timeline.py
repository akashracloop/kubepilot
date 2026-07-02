"""Tests for the incident timeline generator (W7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from kubepilot_orch.agents.finalize import finalize_node
from kubepilot_orch.state import Evidence, InvestigationState, RCAReport, Severity
from kubepilot_orch.timeline import build_timeline


def _ev(
    kind: str, summary: str, minute: int, *, detail: dict | None = None, sev=Severity.INFO
) -> Evidence:
    return Evidence(
        source_agent="x",
        kind=kind,
        summary=summary,
        detail=detail or {},
        severity=sev,
        collected_at=datetime(2026, 7, 2, 10, minute, tzinfo=UTC),
    )


def _state() -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.uuid4(),
        query="why is checkout-service slow?",
        namespace="prod",
        service="checkout-service",
        started_at=datetime(2026, 7, 2, 10, 9, tzinfo=UTC),
        evidence=[
            # Deployment observed late but its detail carries the real deploy time (10:00).
            _ev(
                "recent_deploy",
                "deployed v2.3.1",
                8,
                detail={"deployed_at": "2026-07-02T10:00:00Z", "version": "v2.3.1"},
                sev=Severity.WARNING,
            ),
            _ev("latency_hotspot", "payments-db slow", 8, sev=Severity.ERROR),
            _ev("pod_state", "pod restarting", 7, detail={"status_reason": "CrashLoopBackOff"}),
        ],
        rca=RCAReport(
            root_cause="N+1 query from v2.3.1",
            root_cause_category="LatencyRegression",
            confidence=0.9,
            reasoning="x",
        ),
    )


def test_timeline_orders_by_time_and_uses_deploy_timestamp() -> None:
    state = _state()
    entries = build_timeline(state, finished_at=datetime(2026, 7, 2, 10, 12, tzinfo=UTC))

    # Ordered ascending; the deploy sits first at its real 10:00 time (not 10:08).
    times = [e.at for e in entries]
    assert times == sorted(times)
    assert entries[0].label == "deploy"
    assert entries[0].at == datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    # pod_state uses the k8s reason as the label.
    labels = [e.label for e in entries]
    assert "crashloopbackoff" in labels
    assert "latency_spike" in labels
    # Root-cause bookend last.
    assert entries[-1].label == "root_cause_identified"
    assert "N+1 query" in entries[-1].description


async def test_finalize_populates_timeline() -> None:
    update = await finalize_node(_state())
    assert "timeline" in update
    assert len(update["timeline"]) >= 3
    # Deterministic ordering holds through finalize.
    times = [e.at for e in update["timeline"]]
    assert times == sorted(times)
