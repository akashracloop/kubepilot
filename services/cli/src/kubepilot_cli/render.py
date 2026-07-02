"""Pure formatting helpers for CLI output.

These functions never do I/O — the Typer commands own printing. ``render_report``
and ``render_list`` turn an API ``InvestigationDetail`` dict (whose ``state`` is a
serialized ``InvestigationState``) into human-readable text / a rich table.
"""

from __future__ import annotations

import io
import json
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Console tuned for deterministic, color-free string capture (CI-friendly).
_CAPTURE_WIDTH = 100


def to_json(obj: Any) -> str:
    """Serialize any JSON-ish object to a pretty string (str fallback for the rest)."""
    return json.dumps(obj, indent=2, default=str, sort_keys=False)


def _render_to_str(renderable: RenderableType) -> str:
    console = Console(
        file=io.StringIO(),
        width=_CAPTURE_WIDTH,
        color_system=None,
        force_terminal=False,
        highlight=False,
    )
    console.print(renderable)
    return console.file.getvalue()  # type: ignore[union-attr]


def _short_id(incident_id: str) -> str:
    return incident_id.split("-", 1)[0] if incident_id else "?"


def _confidence_pct(value: Any) -> str:
    try:
        return f"{round(float(value) * 100)}%"
    except (TypeError, ValueError):
        return "n/a"


def render_report(detail: dict[str, Any]) -> str:
    """Render a completed investigation as an RCA report string."""
    state: dict[str, Any] = detail.get("state") or {}
    rca: dict[str, Any] = state.get("rca") or {}

    status = str(detail.get("status", "unknown"))
    incident_id = str(detail.get("incident_id", "?"))

    # Header rendered as literal Text (not a Panel title) so status tokens like
    # "[running]" are shown verbatim rather than parsed as rich markup.
    header = Text()
    header.append("Incident ", style="bold")
    header.append(_short_id(incident_id))
    header.append("  status=", style="bold")
    header.append(f"{status}\n")
    header.append(f"Query:     {detail.get('query', '')}\n")
    header.append(f"Namespace: {detail.get('namespace', '')}")
    if detail.get("service"):
        header.append(f"    Service: {detail.get('service')}")

    if not rca:
        note = detail.get("error") or "No root-cause analysis available yet."
        group = Group(header, Text(f"\n{note}"))
        return _render_to_str(Panel(group, title="RCA Report", title_align="left"))

    body = Text()
    body.append("Root cause\n", style="bold")
    body.append(f"  {rca.get('root_cause', 'unknown')}\n\n")

    category = rca.get("root_cause_category") or "uncategorized"
    body.append("Category:   ", style="bold")
    body.append(f"{category}\n")
    body.append("Confidence: ", style="bold")
    body.append(f"{_confidence_pct(rca.get('confidence'))}\n")

    reasoning = rca.get("reasoning")
    if reasoning:
        body.append("\nReasoning\n", style="bold")
        body.append(f"  {reasoning}\n")

    evidence = state.get("evidence") or []
    if evidence:
        body.append("\nEvidence\n", style="bold")
        for item in evidence:
            severity = item.get("severity", "info")
            agent = item.get("source_agent", "?")
            summary = item.get("summary", "")
            body.append(f"  - [{severity}] {agent}: {summary}\n")

    recommendations = _recommendations(state, rca)
    if recommendations:
        body.append("\nRecommendations\n", style="bold")
        for idx, rec in enumerate(recommendations, start=1):
            body.append(f"  {idx}. {rec['title']}\n")
            if rec.get("rationale"):
                body.append(f"     {rec['rationale']}\n")
            for command in rec.get("commands", []):
                body.append(f"     $ {command}\n")

    group = Group(header, Text(""), body)
    return _render_to_str(Panel(group, title="RCA Report", title_align="left", border_style="cyan"))


def _recommendations(state: dict[str, Any], rca: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer enriched Recommendation objects; fall back to RCA string suggestions."""
    enriched = state.get("recommendations") or []
    if enriched:
        return [
            {
                "title": r.get("title", ""),
                "rationale": r.get("rationale", ""),
                "commands": r.get("commands", []),
            }
            for r in enriched
        ]
    return [
        {"title": str(s), "rationale": "", "commands": []} for s in rca.get("recommendations", [])
    ]


def render_list(items: list[dict[str, Any]]) -> Table:
    """Build a rich table of investigations for the ``list`` command."""
    table = Table(title="Investigations")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Query")
    table.add_column("Namespace")
    table.add_column("Created")

    for item in items:
        table.add_row(
            _short_id(str(item.get("incident_id", ""))),
            str(item.get("status", "")),
            str(item.get("query", "")),
            str(item.get("namespace", "")),
            str(item.get("created_at", "")),
        )
    return table
