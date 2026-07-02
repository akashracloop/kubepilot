"""CLI tests — invoke the Typer app with the client layer patched (no network)."""

from __future__ import annotations

import json

from kubepilot_cli import client
from kubepilot_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def _detail(status: str = "completed", error: str | None = None) -> dict:
    return {
        "incident_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "status": status,
        "query": "why is api failing?",
        "namespace": "prod",
        "service": "api",
        "created_at": "2026-07-02T10:00:00Z",
        "error": error,
        "state": {},
    }


def test_get_output_json_prints_valid_json(monkeypatch) -> None:
    async def fake_get(incident_id, **kwargs):
        return _detail()

    monkeypatch.setattr(client, "get", fake_get)
    result = runner.invoke(app, ["get", "aaaaaaaa", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["incident_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_investigate_no_wait_prints_incident_id(monkeypatch) -> None:
    async def fake_create(query, namespace, service, time_window_minutes, **kwargs):
        return {"incident_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "status": "pending"}

    monkeypatch.setattr(client, "create", fake_create)
    result = runner.invoke(app, ["investigate", "api", "-n", "prod", "--no-wait"])

    assert result.exit_code == 0
    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in result.stdout


def test_failed_investigation_exits_non_zero(monkeypatch) -> None:
    async def fake_get(incident_id, **kwargs):
        return _detail(status="failed", error="orchestrator crashed")

    monkeypatch.setattr(client, "get", fake_get)
    result = runner.invoke(app, ["get", "aaaaaaaa"])

    assert result.exit_code == 1
