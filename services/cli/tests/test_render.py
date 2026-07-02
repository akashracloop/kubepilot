"""Render tests — pure formatting, no I/O."""

from __future__ import annotations

import json

from kubepilot_cli import render
from rich.table import Table

COMPLETED_DETAIL = {
    "incident_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "status": "completed",
    "query": "why is checkout failing?",
    "namespace": "prod",
    "service": "checkout",
    "created_at": "2026-07-02T10:00:00Z",
    "error": None,
    "state": {
        "rca": {
            "root_cause": "OOMKilled: checkout pod exceeded its memory limit",
            "root_cause_category": "resource_exhaustion",
            "confidence": 0.85,
            "reasoning": "Memory usage climbed to the 512Mi limit before restart.",
            "recommendations": ["Raise the memory limit"],
        },
        "evidence": [
            {
                "source_agent": "kubernetes",
                "kind": "pod_state",
                "summary": "checkout-7d restarted 5 times (OOMKilled)",
                "severity": "critical",
            }
        ],
        "recommendations": [
            {
                "title": "Increase memory limit",
                "rationale": "Pod is hitting its 512Mi ceiling.",
                "commands": ["kubectl set resources deploy/checkout --limits=memory=1Gi"],
            }
        ],
    },
}


def test_render_report_includes_root_cause_and_confidence() -> None:
    output = render.render_report(COMPLETED_DETAIL)
    assert "OOMKilled: checkout pod exceeded its memory limit" in output
    assert "85%" in output
    assert "resource_exhaustion" in output
    assert "kubectl set resources deploy/checkout --limits=memory=1Gi" in output


def test_render_report_handles_missing_rca() -> None:
    pending = {
        "incident_id": "1234",
        "status": "running",
        "query": "why?",
        "namespace": "prod",
        "service": None,
        "state": {},
    }
    output = render.render_report(pending)
    assert "running" in output


def test_render_list_builds_rows() -> None:
    items = [
        {
            "incident_id": "aaaaaaaa-1111-2222-3333-444444444444",
            "status": "completed",
            "query": "why is api down?",
            "namespace": "prod",
            "created_at": "2026-07-02T09:00:00Z",
        },
        {
            "incident_id": "bbbbbbbb-1111-2222-3333-444444444444",
            "status": "failed",
            "query": "why is worker stuck?",
            "namespace": "staging",
            "created_at": "2026-07-02T09:05:00Z",
        },
    ]
    table = render.render_list(items)
    assert isinstance(table, Table)
    assert table.row_count == 2
    assert table.columns[0].header == "ID"
    # Short ids are the first UUID segment.
    first_cell = table.columns[0]._cells[0]
    assert first_cell == "aaaaaaaa"


def test_to_json_round_trips() -> None:
    obj = {"incident_id": "abc", "status": "completed", "nested": {"n": 1}}
    text = render.to_json(obj)
    assert json.loads(text) == obj
