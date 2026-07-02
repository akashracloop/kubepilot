"""Investigation state schema and version-aware checkpoint loader.

See docs/ARCHITECTURE.md §3.2.1 for the 5-rule discipline this module enforces:
additive-only between minor bumps; migration functions for major bumps;
fixture-replay tests in tests/fixtures/checkpoints/ guarantee backward compat.

Reducer annotations (Annotated[..., reducer]) are read by LangGraph at graph
compile time so parallel agent updates *merge* rather than overwrite. Pydantic
ignores the metadata so wire format is unchanged.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field

# Bump this constant when state shape changes. Additive bumps need no migration entry.
CURRENT_SCHEMA_VERSION: int = 1


def _merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Reducer for dict fields under LangGraph parallel updates — right-wins per key."""
    return {**a, **b}


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Evidence(BaseModel):
    """A single piece of evidence collected by a sub-agent."""

    source_agent: str
    kind: str  # e.g. "pod_state", "metric_anomaly", "log_pattern"
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = Severity.INFO
    collected_at: datetime


class AgentOutput(BaseModel):
    """Structured output from a single agent run."""

    agent_name: str
    succeeded: bool
    evidence: list[Evidence] = Field(default_factory=list)
    notes: str | None = None
    tokens_used: int = 0
    latency_ms: int = 0


class RCAReport(BaseModel):
    """Final root-cause analysis from the RCA agent."""

    root_cause: str
    root_cause_category: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[int] = Field(default_factory=list)
    reasoning: str
    recommendations: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    """Enriched, ranked remediation suggestion from the Recommendation agent.

    Phase 1: ``commands`` are SUGGESTIONS for a human SRE to execute. They are
    NEVER auto-run. Phase 4 introduces HITL approval + the k8s-write-mcp server
    that can actually invoke them, gated on ``requires_approval`` + the policy
    engine.
    """

    title: str  # short imperative phrase, e.g. "Roll back deployment"
    rationale: str
    commands: list[str] = Field(default_factory=list)  # kubectl/helm/...
    risk: str = "medium"  # "low" | "medium" | "high"
    reversibility: str = "reversible"  # "reversible" | "partial" | "irreversible"
    priority: int = 1  # 1 = highest
    requires_approval: bool = True  # P4 hint — all writes need approval initially
    estimated_blast_radius: str | None = None  # P4 informational; P1 may set or omit


class InvestigationState(BaseModel):
    """Top-level LangGraph state for a single investigation.

    Persistence note: this object is serialized to Postgres at every node
    transition. Schema changes must follow docs/ARCHITECTURE.md §3.2.1.
    """

    # Schema versioning — see CURRENT_SCHEMA_VERSION above.
    schema_version: int = CURRENT_SCHEMA_VERSION

    # Identity
    incident_id: UUID
    query: str
    namespace: str
    service: str | None = None
    time_window_minutes: int = 30

    # Progress
    current_step: str = "initialized"
    completed_agents: Annotated[list[str], operator.add] = Field(default_factory=list)

    # Accumulated outputs — Annotated reducers let parallel agents merge their
    # contributions instead of overwriting each other when wired into LangGraph.
    evidence: Annotated[list[Evidence], operator.add] = Field(default_factory=list)
    agent_outputs: Annotated[dict[str, AgentOutput], _merge_dicts] = Field(default_factory=dict)

    # Final result
    rca: RCAReport | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
    confidence: float | None = None
    failed_with: str | None = None

    # Trace metadata
    started_at: datetime
    finished_at: datetime | None = None


class CheckpointMigrationError(Exception):
    """Raised when a checkpoint blob cannot be migrated to the current schema."""

    def __init__(self, from_: int, to: int, missing_step: int | None = None) -> None:
        self.from_ = from_
        self.to = to
        self.missing_step = missing_step
        msg = f"Cannot migrate checkpoint from v{from_} to v{to}"
        if missing_step is not None:
            msg += f" (missing migration v{missing_step} -> v{missing_step + 1})"
        super().__init__(msg)


# Migrations registry. Populated ONLY for major (shape-breaking) version bumps.
# Additive-only changes do not need entries — defaults on new fields handle them.
#
# Signature: a migration takes the prior-version dict blob and returns the next-version blob.
# Example for a future v1->v2 break:
#     def _v1_to_v2(blob: dict[str, Any]) -> dict[str, Any]:
#         blob["new_required_field"] = derive_from(blob)
#         blob["schema_version"] = 2
#         return blob
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def load_checkpoint(blob: dict[str, Any]) -> InvestigationState:
    """Load a checkpoint dict, applying migrations to reach the current schema.

    Old checkpoints written under prior versions must either deserialize directly
    (additive-only changes) or be migrated via the MIGRATIONS registry.
    """
    version = blob.get("schema_version", 1)

    while version < CURRENT_SCHEMA_VERSION:
        migrate = MIGRATIONS.get(version)
        if migrate is None:
            raise CheckpointMigrationError(
                from_=version,
                to=CURRENT_SCHEMA_VERSION,
                missing_step=version,
            )
        blob = migrate(blob)
        version = blob.get("schema_version", version + 1)

    return InvestigationState.model_validate(blob)


def dump_checkpoint(state: InvestigationState) -> dict[str, Any]:
    """Serialize state to a JSON-safe dict for persistence."""
    return state.model_dump(mode="json")
