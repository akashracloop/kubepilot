from __future__ import annotations

from fastapi.testclient import TestClient
from kubepilot_api.config import ApiSettings
from kubepilot_api.main import build_app
from kubepilot_api.repository import InMemoryInvestigationRepository


def _app() -> TestClient:
    """Build the FastAPI app with in-memory deps so we don't touch Postgres/LLMs in tests."""
    settings = ApiSettings(storage="memory")
    settings.auth.api_key = None  # auth disabled for health tests
    repo = InMemoryInvestigationRepository()
    # Health endpoints don't touch the graph; pass a sentinel so build_app doesn't construct
    # the production graph (which would require LLM creds).
    return TestClient(build_app(settings=settings, repo=repo, compiled_graph=object()))


def test_health_returns_ok() -> None:
    r = _app().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_returns_ok() -> None:
    r = _app().get("/ready")
    assert r.status_code == 200
