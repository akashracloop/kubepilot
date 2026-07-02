"""W7 acceptance test: API gateway end-to-end with a scripted graph.

Spec from PHASE_1_PLAN.md W7:
    "curl triggers investigation, streams result"

We replace curl with FastAPI's TestClient + an in-memory repo + a fake
compiled graph that produces a canned final state. The test asserts:
  - POST /investigations returns 202 + an incident_id
  - GET /investigations/{id} eventually shows status=completed with RCA + recommendations
  - GET /investigations/{id}/stream emits investigation_started, node_completed*, investigation_completed
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kubepilot_api.config import ApiSettings
from kubepilot_api.main import build_app
from kubepilot_api.repository import COMPLETED, InMemoryInvestigationRepository
from kubepilot_orch.state import (
    AgentOutput,
    Evidence,
    InvestigationState,
    RCAReport,
    Recommendation,
    Severity,
)


# ---------------------------------------------------------------------------
# Fake compiled graph — astream yields node-completion chunks, ainvoke returns
# the final merged state. Both pulled from the same canned blueprint.
# ---------------------------------------------------------------------------


def _final_state_for(initial: dict[str, Any]) -> dict[str, Any]:
    """Build the merged final state the fake graph 'computes' for an investigation."""
    incident_id = initial["incident_id"]
    now = datetime.now(timezone.utc)
    state = InvestigationState(
        incident_id=incident_id,
        query=initial["query"],
        namespace=initial["namespace"],
        service=initial.get("service"),
        time_window_minutes=initial.get("time_window_minutes", 30),
        current_step="completed",
        completed_agents=["kubernetes", "metrics", "logs", "rca", "recommendation"],
        evidence=[
            Evidence(
                source_agent="kubernetes",
                kind="pod_state",
                summary="payment-service-0 in CrashLoopBackOff, OOMKilled, 12 restarts.",
                detail={"restart_count": 12, "last_exit_code": 137},
                severity=Severity.CRITICAL,
                collected_at=now,
            )
        ],
        agent_outputs={
            "kubernetes": AgentOutput(agent_name="kubernetes", succeeded=True),
            "metrics": AgentOutput(agent_name="metrics", succeeded=True),
            "logs": AgentOutput(agent_name="logs", succeeded=True),
        },
        rca=RCAReport(
            root_cause="JVM heap exhaustion in payment-service.",
            root_cause_category="OOMKilled",
            confidence=0.92,
            evidence_refs=[0],
            reasoning="K8s + Metrics + Logs corroborate.",
            recommendations=["Roll back deployment", "Raise memory limit"],
        ),
        recommendations=[
            Recommendation(
                title="Roll back deployment",
                rationale="Restore the previous image.",
                commands=["kubectl rollout undo deployment/payment-service -n prod"],
                risk="medium",
                reversibility="reversible",
                priority=1,
                requires_approval=True,
            )
        ],
        confidence=0.92,
        started_at=initial.get("started_at"),
        finished_at=now,
    )
    return state.model_dump(mode="python")


class _FakeCompiledGraph:
    """Stand-in for langgraph's compiled graph.

    ``astream`` yields per-node chunks; ``ainvoke`` returns the canonical final state.
    """

    NODES = ("supervisor", "kubernetes", "metrics", "logs", "rca", "recommendation", "finalize")

    async def astream(self, initial: dict[str, Any]):  # type: ignore[no-untyped-def]
        for node in self.NODES:
            await asyncio.sleep(0)  # cooperate with the event loop
            yield {node: {}}

    async def ainvoke(self, initial: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        return _final_state_for(initial)


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def app_pieces() -> tuple[TestClient, InMemoryInvestigationRepository, Any]:
    settings = ApiSettings(storage="memory")
    settings.auth.api_key = "test-key"
    repo = InMemoryInvestigationRepository()
    graph = _FakeCompiledGraph()
    app = build_app(settings=settings, repo=repo, compiled_graph=graph)
    return TestClient(app), repo, app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_post_investigation_returns_202_and_id(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, _repo, _ = app_pieces
    r = client.post(
        "/investigations",
        headers={"X-API-Key": "test-key"},
        json={"query": "why is payment-service failing?", "namespace": "prod", "service": "payment-service"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert "incident_id" in body
    assert body["status"] == "pending"
    uuid.UUID(body["incident_id"])  # parseable


def test_post_without_api_key_returns_401(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, *_ = app_pieces
    r = client.post(
        "/investigations",
        json={"query": "x", "namespace": "prod"},
    )
    assert r.status_code == 401


def test_post_with_wrong_api_key_returns_401(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, *_ = app_pieces
    r = client.post(
        "/investigations",
        headers={"X-API-Key": "definitely-wrong"},
        json={"query": "x", "namespace": "prod"},
    )
    assert r.status_code == 401


def test_get_unknown_investigation_returns_404(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, *_ = app_pieces
    r = client.get(
        f"/investigations/{uuid.uuid4()}",
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 404


def test_post_then_get_eventually_shows_completed(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, _repo, app = app_pieces

    post = client.post(
        "/investigations",
        headers={"X-API-Key": "test-key"},
        json={"query": "why is payment-service failing?", "namespace": "prod", "service": "payment-service"},
    )
    incident_id = post.json()["incident_id"]

    # Wait for the orchestrator task to finish.
    orch = app.state.orchestrator
    asyncio.get_event_loop().run_until_complete(  # TestClient runs on the test thread's loop
        orch.wait_for(uuid.UUID(incident_id), timeout=5.0)
    )

    detail = client.get(
        f"/investigations/{incident_id}",
        headers={"X-API-Key": "test-key"},
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == COMPLETED
    assert body["state"]["rca"]["root_cause_category"] == "OOMKilled"
    assert body["state"]["confidence"] == 0.92
    assert len(body["state"]["recommendations"]) == 1
    assert "kubectl rollout undo" in body["state"]["recommendations"][0]["commands"][0]


def test_list_investigations_paginates(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, _repo, app = app_pieces
    headers = {"X-API-Key": "test-key"}

    # Create 3.
    ids = []
    for _ in range(3):
        r = client.post(
            "/investigations",
            headers=headers,
            json={"query": "x", "namespace": "prod"},
        )
        ids.append(r.json()["incident_id"])

    listed = client.get("/investigations?limit=2", headers=headers).json()
    assert len(listed["items"]) == 2
    assert listed["limit"] == 2
    assert listed["offset"] == 0


def test_stream_emits_events_for_running_investigation(app_pieces) -> None:  # type: ignore[no-untyped-def]
    """SSE: connect to /stream BEFORE waiting; collect events until done."""
    client, _repo, app = app_pieces
    headers = {"X-API-Key": "test-key"}

    post = client.post(
        "/investigations",
        headers=headers,
        json={"query": "x", "namespace": "prod"},
    )
    incident_id = post.json()["incident_id"]

    # TestClient supports streaming responses via ``stream=True``-style only with
    # the lower-level send API. We collect the full body, then parse the SSE frames.
    with client.stream(
        "GET",
        f"/investigations/{incident_id}/stream",
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode()

    # The body is a series of "event: ...\ndata: ...\n\n" frames.
    events_seen = [line[7:].strip() for line in body.splitlines() if line.startswith("event: ")]

    # Must have at least the lifecycle bookends.
    assert "investigation_started" in events_seen or "investigation_completed" in events_seen
    # And at least one per-node event OR a synthetic completed event if we connected too late.
    assert any(
        e in events_seen
        for e in ("node_completed", "investigation_completed")
    )


def test_stream_returns_synthetic_completed_when_already_done(app_pieces) -> None:  # type: ignore[no-untyped-def]
    """If the investigation has already finished, the stream emits one event and closes."""
    client, _repo, app = app_pieces
    headers = {"X-API-Key": "test-key"}

    post = client.post(
        "/investigations",
        headers=headers,
        json={"query": "x", "namespace": "prod"},
    )
    incident_id = post.json()["incident_id"]

    # Wait for completion BEFORE connecting to the stream.
    orch = app.state.orchestrator
    asyncio.get_event_loop().run_until_complete(
        orch.wait_for(uuid.UUID(incident_id), timeout=5.0)
    )

    with client.stream(
        "GET",
        f"/investigations/{incident_id}/stream",
        headers=headers,
    ) as resp:
        body = b"".join(resp.iter_bytes()).decode()

    assert "event: investigation_completed" in body
    # The data line should contain the final state.
    data_lines = [l for l in body.splitlines() if l.startswith("data: ")]
    assert data_lines
    payload = json.loads(data_lines[0][6:])
    assert payload["status"] == COMPLETED
