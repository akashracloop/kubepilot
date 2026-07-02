"""Incident timeline assembly (Phase 2).

Builds an ordered chronology from the evidence the specialists collected plus
deploy events, so the UI/API can show "deploy → first anomaly → root cause".

Deterministic by design: ordering comes from evidence timestamps (never an LLM),
and labels come from a stable kind→label mapping. This is more reliable and
testable than asking a model to order events; an LLM labeling refinement is a
possible later enhancement, not a dependency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kubepilot_orch.state import Evidence, InvestigationState, Severity, TimelineEntry

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
