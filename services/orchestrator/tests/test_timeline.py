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


# ---- Optional LLM label refinement (fails open) ---------------------------

import json  # noqa: E402

import pytest  # noqa: E402
from kubepilot_orch.testing import ScriptedLLM, build_router, llm_text  # noqa: E402
from kubepilot_orch.timeline import TimelineEntry, refine_labels  # noqa: E402


def _entry(label: str, minute: int) -> TimelineEntry:
    return TimelineEntry(
        at=datetime(2026, 7, 2, 10, minute, tzinfo=UTC),
        label=label,
        description=f"event {label}",
        source="test",
        severity=Severity.INFO,
    )


@pytest.mark.asyncio
async def test_refine_labels_applies_model_labels_in_order() -> None:
    entries = [_entry("deploy", 0), _entry("log_errors", 5)]
    scripted = ScriptedLLM(responses=[llm_text(json.dumps(["deploy_started", "error_spike"]))])
    refined = await refine_labels(entries, llm=build_router(scripted))
    assert [e.label for e in refined] == ["deploy_started", "error_spike"]
    # Ordering / timestamps untouched.
    assert [e.at for e in refined] == [e.at for e in entries]


@pytest.mark.asyncio
async def test_refine_labels_fails_open_on_bad_output() -> None:
    entries = [_entry("deploy", 0), _entry("log_errors", 5)]
    for bad in ("not json", json.dumps(["only-one"]), json.dumps({"x": 1})):
        scripted = ScriptedLLM(responses=[llm_text(bad)])
        refined = await refine_labels(entries, llm=build_router(scripted))
        assert [e.label for e in refined] == ["deploy", "log_errors"]  # unchanged


@pytest.mark.asyncio
async def test_refine_labels_empty_is_noop() -> None:
    scripted = ScriptedLLM(responses=[])  # must not be called
    assert await refine_labels([], llm=build_router(scripted)) == []
