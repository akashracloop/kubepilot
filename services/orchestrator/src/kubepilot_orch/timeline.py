"""Incident timeline assembly (Phase 2, + optional LLM labeling).

Builds an ordered chronology from the evidence the specialists collected plus
deploy events, so the UI/API can show "deploy → first anomaly → root cause".

Deterministic by design: **ordering** comes from evidence timestamps (never an
LLM), and labels come from a stable kind→label mapping. An *optional* LLM pass
(:func:`refine_labels`) can polish the labels into more human-friendly phrases
without touching the ordering; it fails open to the deterministic labels, so it
is a refinement, never a dependency.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from kubepilot_orch.state import Evidence, InvestigationState, Severity, TimelineEntry

if TYPE_CHECKING:
    from kubepilot_orch.llm.router import LLMRouter

log = structlog.get_logger(__name__)

# Evidence kind → short timeline label.
_KIND_LABEL = {
    "recent_deploy": "deploy",
    "recent_commit": "commit",
    "pipeline_status": "pipeline",
    "latency_hotspot": "latency_spike",
    "failed_span": "failed_span",
    "dependency_edge": "dependency_error",
    "metric_anomaly": "metric_anomaly",
    "log_pattern": "log_errors",
    "event": "k8s_event",
    "deployment_state": "rollout",
    "pod_state": "pod_state",
}


def _label_for(ev: Evidence) -> str:
    if ev.kind == "pod_state":
        reason = str(
            ev.detail.get("status_reason") or ev.detail.get("last_termination_reason") or ""
        )
        if reason:
            return reason.lower()  # e.g. "oomkilled", "crashloopbackoff"
    return _KIND_LABEL.get(ev.kind, ev.kind)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def build_timeline(
    state: InvestigationState, *, finished_at: datetime | None = None
) -> list[TimelineEntry]:
    """Assemble an ordered timeline for the investigation.

    ``finished_at`` overrides ``state.finished_at`` for the root-cause bookend,
    since finalize computes the timeline in the same step it stamps finished_at.
    """
    entries: list[TimelineEntry] = []
    concluded_at = finished_at or state.finished_at

    # A deploy's own timestamp (detail.deployed_at) usually precedes when the
    # Deployment agent observed it — surface the deploy at its real time.
    for ev in state.evidence:
        deployed_at = (
            _parse_dt(ev.detail.get("deployed_at")) if ev.kind == "recent_deploy" else None
        )
        entries.append(
            TimelineEntry(
                at=deployed_at or ev.collected_at,
                label=_label_for(ev),
                description=ev.summary,
                source=ev.source_agent,
                severity=ev.severity,
            )
        )

    # Bookend with the root-cause conclusion when we have one.
    if state.rca is not None and concluded_at is not None:
        entries.append(
            TimelineEntry(
                at=concluded_at,
                label="root_cause_identified",
                description=state.rca.root_cause,
                source="rca",
                severity=Severity.INFO,
            )
        )

    entries.sort(key=lambda e: e.at)
    return entries


_REFINE_SYSTEM = (
    "You relabel incident-timeline events. You are given an ordered list of events "
    "(index, current_label, description). Return ONLY a JSON array of strings — one "
    "short snake_case label (<= 3 words) per event, in the SAME order and length. "
    "Do not reorder, add, or drop events. Keep a good current_label if it's already apt."
)


async def refine_labels(entries: list[TimelineEntry], *, llm: LLMRouter) -> list[TimelineEntry]:
    """Optionally polish timeline labels with an LLM (ordering untouched).

    Fails open: any invalid/mismatched model output leaves the deterministic labels
    in place. Only the ``label`` field changes; timestamps/order/severity are kept.
    """
    from kubepilot_orch.llm.base import Message, Role
    from kubepilot_orch.llm.parsing import clean_json

    if not entries:
        return entries

    listing = "\n".join(
        f"{i}. current_label={e.label!r} description={e.description!r}"
        for i, e in enumerate(entries)
    )
    try:
        resp = await llm.chat(
            role=Role.SUMMARIZATION,
            messages=[
                Message(role="system", content=_REFINE_SYSTEM),
                Message(role="user", content=listing),
            ],
            temperature=0.0,
        )
        labels = json.loads(clean_json(resp.content))
    except (ValueError, TypeError) as e:
        log.warning("timeline_refine_failed", error=str(e))
        return entries

    if not isinstance(labels, list) or len(labels) != len(entries):
        log.warning(
            "timeline_refine_shape_mismatch", got=len(labels) if isinstance(labels, list) else None
        )
        return entries

    refined: list[TimelineEntry] = []
    for entry, label in zip(entries, labels, strict=True):
        new_label = str(label).strip() if label else entry.label
        refined.append(entry.model_copy(update={"label": new_label or entry.label}))
    return refined
