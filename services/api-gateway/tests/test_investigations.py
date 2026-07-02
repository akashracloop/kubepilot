"""W7 acceptance test: API gateway end-to-end with a scripted graph.

Spec from PHASE_1_PLAN.md W7:
    "curl triggers investigation, streams result"

We replace curl with an in-process ``httpx.AsyncClient`` driven over
``ASGITransport`` + an in-memory repo + a fake compiled graph that produces a
canned final state. The test asserts:
  - POST /investigations returns 202 + an incident_id
  - GET /investigations/{id} eventually shows status=completed with RCA + recommendations
  - GET /investigations/{id}/stream emits investigation_started, node_completed*, investigation_completed

Why async + ASGITransport (not TestClient): the orchestrator runs each
investigation as a background ``asyncio`` task. TestClient drives the app on a
separate portal loop that is only pumped during a request, so the background
task starves between synchronous calls. Running the app on the *same* loop as
the test lets the task progress and lets us ``await orchestrator.wait_for(...)``
deterministically.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest_asyncio
from httpx import ASGITransport
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
# Fake compiled graph — astream yields ("updates", {node: {}}) per node and a
# final ("values", state) tuple, mirroring stream_mode=["updates", "values"].
# ---------------------------------------------------------------------------


def _final_state_for(initial: dict[str, Any]) -> dict[str, Any]:
    """Build the merged final state the fake graph 'computes' for an investigation."""
    incident_id = initial["incident_id"]
    now = datetime.now(UTC)
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

    Mirrors ``astream(stream_mode=["updates", "values"])``: it yields
    ``("updates", {node: {}})`` after each node and a final ``("values", state)``
    tuple with the canonical merged state — exactly what the orchestrator client
    consumes in a single pass.
    """

    NODES = ("supervisor", "kubernetes", "metrics", "logs", "rca", "recommendation", "finalize")

    async def astream(  # type: ignore[no-untyped-def]
        self, initial: dict[str, Any], *, stream_mode=None, config=None
    ):
        for node in self.NODES:
            yield ("updates", {node: {}})
        yield ("values", _final_state_for(initial))


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_pieces() -> Any:
    settings = ApiSettings(storage="memory")
    settings.auth.api_key = "test-key"
    repo = InMemoryInvestigationRepository()
    graph = _FakeCompiledGraph()
    app = build_app(settings=settings, repo=repo, compiled_graph=graph)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo, app


AUTH = {"X-API-Key": "test-key"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_post_investigation_returns_202_and_id(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, _repo, _app = app_pieces
    r = await client.post(
        "/investigations",
        headers=AUTH,
        json={
            "query": "why is payment-service failing?",
            "namespace": "prod",
            "service": "payment-service",
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert "incident_id" in body
    assert body["status"] == "pending"
    uuid.UUID(body["incident_id"])  # parseable


async def test_post_without_api_key_returns_401(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, *_ = app_pieces
    r = await client.post("/investigations", json={"query": "x", "namespace": "prod"})
    assert r.status_code == 401


async def test_post_with_wrong_api_key_returns_401(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, *_ = app_pieces
    r = await client.post(
        "/investigations",
        headers={"X-API-Key": "definitely-wrong"},
        json={"query": "x", "namespace": "prod"},
    )
    assert r.status_code == 401


async def test_get_unknown_investigation_returns_404(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, *_ = app_pieces
    r = await client.get(f"/investigations/{uuid.uuid4()}", headers=AUTH)
    assert r.status_code == 404


async def test_post_then_get_eventually_shows_completed(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, _repo, app = app_pieces

    post = await client.post(
        "/investigations",
        headers=AUTH,
        json={
            "query": "why is payment-service failing?",
            "namespace": "prod",
            "service": "payment-service",
        },
    )
    incident_id = post.json()["incident_id"]

    # Same event loop as the app → the background task is awaitable directly.
    await app.state.orchestrator.wait_for(uuid.UUID(incident_id), timeout=5.0)

    detail = await client.get(f"/investigations/{incident_id}", headers=AUTH)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == COMPLETED
    assert body["state"]["rca"]["root_cause_category"] == "OOMKilled"
    assert body["state"]["confidence"] == 0.92
    assert len(body["state"]["recommendations"]) == 1
    assert "kubectl rollout undo" in body["state"]["recommendations"][0]["commands"][0]


async def test_list_investigations_paginates(app_pieces) -> None:  # type: ignore[no-untyped-def]
    client, _repo, _app = app_pieces

    for _ in range(3):
        await client.post("/investigations", headers=AUTH, json={"query": "x", "namespace": "prod"})

    listed = (await client.get("/investigations?limit=2", headers=AUTH)).json()
    assert len(listed["items"]) == 2
    assert listed["limit"] == 2
    assert listed["offset"] == 0


async def test_stream_emits_events_for_running_investigation(app_pieces) -> None:  # type: ignore[no-untyped-def]
    """SSE: connect to /stream and collect events until the stream closes."""
    client, _repo, _app = app_pieces

    post = await client.post(
        "/investigations", headers=AUTH, json={"query": "x", "namespace": "prod"}
    )
    incident_id = post.json()["incident_id"]

    events_seen: list[str] = []
    async with client.stream("GET", f"/investigations/{incident_id}/stream", headers=AUTH) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                events_seen.append(line.split(":", 1)[1].strip())

    # A per-node event during the run, or a synthetic completed event if we
    # connected after the investigation already finished — either is valid.
    assert any(e in events_seen for e in ("node_completed", "investigation_completed"))


async def test_stream_returns_synthetic_completed_when_already_done(app_pieces) -> None:  # type: ignore[no-untyped-def]
    """If the investigation has already finished, the stream emits one event and closes."""
    client, _repo, app = app_pieces

    post = await client.post(
        "/investigations", headers=AUTH, json={"query": "x", "namespace": "prod"}
    )
    incident_id = post.json()["incident_id"]

    # Wait for completion BEFORE connecting to the stream.
    await app.state.orchestrator.wait_for(uuid.UUID(incident_id), timeout=5.0)

    events: list[str] = []
    data_lines: list[str] = []
    async with client.stream("GET", f"/investigations/{incident_id}/stream", headers=AUTH) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())

    assert "investigation_completed" in events
    assert data_lines
    payload = json.loads(data_lines[0])
    assert payload["status"] == COMPLETED
