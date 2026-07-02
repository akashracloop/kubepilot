"""Slack Block Kit builders for investigation results.

Pure functions that turn an ``InvestigationDetail`` dict (as returned by
``GET /investigations/{id}``) into Block Kit blocks. No Slack SDK is imported
here so the builders stay trivially unit-testable.

The ``detail["state"]`` dict is a serialized ``InvestigationState`` — the fields
consumed here are ``state["rca"]`` (root_cause, root_cause_category, confidence)
and ``state["recommendations"]`` (title, commands, ...).
"""

from __future__ import annotations

from typing import Any

_STATUS_EMOJI = {
    "completed": ":white_check_mark:",
    "failed": ":x:",
}
_MAX_RECOMMENDATIONS = 3


def result_card(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Block Kit blocks summarizing a finished investigation."""
    status = str(detail.get("status", "unknown"))
    state = detail.get("state") or {}
    rca = state.get("rca") or {}

    blocks: list[dict[str, Any]] = [_header(status)]

    context_line = _context_line(detail)
    if context_line:
        blocks.append(_section(context_line))

    if status == "failed" or not rca:
        blocks.append(_section(_failure_text(detail)))
        blocks.append(_footer(detail))
        return blocks

    blocks.append(_section(_root_cause_text(rca, state)))

    for rec in _top_recommendations(state):
        blocks.append(_section(rec))

    blocks.append(_footer(detail))
    return blocks


def progress_message(event_type: str, node: str | None = None) -> str:
    """Short human-readable line for a streamed progress event (optional)."""
    label = node or event_type
    pretty = label.replace("_", " ").strip() or "working"
    return f":hourglass_flowing_sand: {pretty}…"


# action_id encoding: "<decision>:<incident_id>:<action_index>" so the button
# handler can route an approve/reject back to the API without extra state.
def encode_action_id(decision: str, incident_id: str, action_index: int) -> str:
    return f"{decision}:{incident_id}:{action_index}"


def decode_action_id(action_id: str) -> tuple[str, str, int]:
    decision, incident_id, index = action_id.split(":", 2)
    return decision, incident_id, int(index)


def approval_card(incident_id: str, approval: dict[str, Any]) -> list[dict[str, Any]]:
    """Block Kit card for a pending remediation plan, with approve/reject buttons.

    ``approval`` is the ``GET /investigations/{id}/approval`` response. Each
    curated action gets approve + reject buttons; the blast radius and
    reversibility are shown so the approver sees the impact.
    """
    status = str(approval.get("status", "unknown"))
    actions = approval.get("actions", [])
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":rotating_light: Remediation approval",
                "emoji": True,
            },
        },
        _section(f"*Status:* {status}"),
    ]
    if not actions:
        blocks.append(_section("_No remediation actions to approve._"))
        return blocks

    for a in actions:
        blocks.append(_section(_action_text(a)))
        blocks.append(_approval_buttons(incident_id, int(a["index"])))
    return blocks


def _action_text(action: dict[str, Any]) -> str:
    br = action.get("blast_radius") or {}
    impact = (
        f" · ~{br.get('pods_affected')} pod(s), ~{br.get('traffic_percent')}% traffic" if br else ""
    )
    deps = f" · dependents: {', '.join(br.get('dependents', []))}" if br.get("dependents") else ""
    return (
        f"*{action['tool']}* `{action['target']}` in `{action['namespace']}` "
        f"({action['reversibility']}, approve:{action['approval_tier']}){impact}{deps}\n"
        f"{action.get('rationale', '')}"
    )


def _approval_buttons(incident_id: str, index: int) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "style": "primary",
                "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                "action_id": encode_action_id("approve", incident_id, index),
                "value": encode_action_id("approve", incident_id, index),
            },
            {
                "type": "button",
                "style": "danger",
                "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                "action_id": encode_action_id("reject", incident_id, index),
                "value": encode_action_id("reject", incident_id, index),
            },
        ],
    }


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------


def _header(status: str) -> dict[str, Any]:
    emoji = _STATUS_EMOJI.get(status, ":mag:")
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{emoji} Investigation {status}",
            "emoji": True,
        },
    }


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context_line(detail: dict[str, Any]) -> str:
    parts: list[str] = []
    service = detail.get("service")
    namespace = detail.get("namespace")
    if service:
        parts.append(f"*Service:* `{service}`")
    if namespace:
        parts.append(f"*Namespace:* `{namespace}`")
    return "   ".join(parts)


def _root_cause_text(rca: dict[str, Any], state: dict[str, Any]) -> str:
    root_cause = rca.get("root_cause") or "No root cause identified."
    category = rca.get("root_cause_category")
    confidence = rca.get("confidence")
    if confidence is None:
        confidence = state.get("confidence")

    lines = [f"*Root cause:* {root_cause}"]
    if category:
        lines.append(f"*Category:* {category}")
    lines.append(f"*Confidence:* {_format_confidence(confidence)}")
    return "\n".join(lines)


def _top_recommendations(state: dict[str, Any]) -> list[str]:
    recs = state.get("recommendations") or []
    out: list[str] = []
    for rec in recs[:_MAX_RECOMMENDATIONS]:
        if not isinstance(rec, dict):
            continue
        title = rec.get("title") or "Recommendation"
        commands = rec.get("commands") or []
        text = f"*{title}*"
        if commands:
            text += f"\n```{commands[0]}```"
        out.append(text)
    return out


def _failure_text(detail: dict[str, Any]) -> str:
    state = detail.get("state") or {}
    error = detail.get("error") or state.get("failed_with") or "No details available."
    return f"*Investigation did not complete.*\n{error}"


def _footer(detail: dict[str, Any]) -> dict[str, Any]:
    incident_id = detail.get("incident_id", "unknown")
    return {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"KubePilot AI · incident `{incident_id}`"},
        ],
    }


def _format_confidence(confidence: Any) -> str:
    if not isinstance(confidence, int | float):
        return "N/A"
    return f"{confidence * 100:.0f}%"
