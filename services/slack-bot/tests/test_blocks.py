"""Tests for the Block Kit result-card builders."""

from __future__ import annotations

from typing import Any

from kubepilot_slack.blocks import progress_message, result_card


def _flatten_text(blocks: list[dict[str, Any]]) -> str:
    """Concatenate all rendered text in a block list for easy assertions."""
    chunks: list[str] = []
    for block in blocks:
        text = block.get("text")
        if isinstance(text, dict):
            chunks.append(text.get("text", ""))
        for element in block.get("elements", []):
            if isinstance(element, dict):
                chunks.append(element.get("text", ""))
    return "\n".join(chunks)


def _completed_detail() -> dict[str, Any]:
    return {
        "incident_id": "11111111-1111-1111-1111-111111111111",
        "status": "completed",
        "service": "payment-service",
        "namespace": "prod",
        "error": None,
        "state": {
            "rca": {
                "root_cause": "OOMKilled due to a memory leak in the checkout path",
                "root_cause_category": "resource_exhaustion",
                "confidence": 0.82,
            },
            "recommendations": [
                {
                    "title": "Raise the memory limit",
                    "commands": [
                        "kubectl set resources deploy/payment-service --limits=memory=1Gi"
                    ],
                },
                {
                    "title": "Roll back deployment",
                    "commands": ["kubectl rollout undo deploy/payment-service"],
                },
                {"title": "Add an alert", "commands": ["promtool ..."]},
                {"title": "Fourth (should be dropped)", "commands": ["echo no"]},
            ],
        },
    }


def test_result_card_contains_root_cause_confidence_and_command() -> None:
    blocks = result_card(_completed_detail())
    text = _flatten_text(blocks)

    assert blocks[0]["type"] == "header"
    assert "completed" in blocks[0]["text"]["text"]
    assert "OOMKilled due to a memory leak" in text
    assert "resource_exhaustion" in text
    assert "82%" in text  # confidence rendered as a percentage
    assert "kubectl set resources deploy/payment-service" in text  # first command
    # Only the top 3 recommendations are shown.
    assert "Fourth (should be dropped)" not in text


def test_all_blocks_are_valid_dicts_with_type() -> None:
    blocks = result_card(_completed_detail())
    assert all(isinstance(b, dict) and "type" in b for b in blocks)


def test_failed_investigation_is_handled_gracefully() -> None:
    detail = {
        "incident_id": "22222222-2222-2222-2222-222222222222",
        "status": "failed",
        "service": None,
        "namespace": "prod",
        "error": "orchestrator crashed",
        "state": {},
    }
    blocks = result_card(detail)
    text = _flatten_text(blocks)
    assert ":x:" in blocks[0]["text"]["text"]
    assert "orchestrator crashed" in text


def test_empty_state_does_not_crash() -> None:
    detail = {"incident_id": "abc", "status": "completed", "state": {}}
    blocks = result_card(detail)
    text = _flatten_text(blocks)
    assert "No root cause" in text or "did not complete" in text
    assert all("type" in b for b in blocks)


def test_progress_message_is_short_text() -> None:
    msg = progress_message("agent_started", node="metrics_agent")
    assert "metrics agent" in msg
