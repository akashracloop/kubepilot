"""Tests for InvestigationState + checkpoint versioning.

The fixture-replay tests below are the central architectural commitment from
docs/ARCHITECTURE.md §3.2.1: every historical schema version has a sample
checkpoint in tests/fixtures/checkpoints/ that MUST continue to load with
the current code. A failing test here means a schema change broke backward
compat — either make the change additive, or add a migration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from kubepilot_orch.state import (
    CURRENT_SCHEMA_VERSION,
    CheckpointMigrationError,
    InvestigationState,
    Severity,
    dump_checkpoint,
    load_checkpoint,
)


def _load_fixture(fixtures_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((fixtures_dir / "checkpoints" / name).read_text())


def test_current_schema_version_is_positive() -> None:
    assert CURRENT_SCHEMA_VERSION >= 1


def test_v1_fixture_loads_under_current_schema(fixtures_dir: Path) -> None:
    """The single most important test in this module.

    Every committed fixture must load cleanly under the current code. If this
    fails, either revert the schema change or add a migration.
    """
    blob = _load_fixture(fixtures_dir, "v1_sample.json")
    state = load_checkpoint(blob)

    assert isinstance(state, InvestigationState)
    assert state.incident_id == UUID("11111111-1111-1111-1111-111111111111")
    assert state.namespace == "prod"
    assert state.service == "payment-service"
    assert state.rca is not None
    assert state.rca.confidence == pytest.approx(0.92)
    assert state.rca.root_cause_category == "OOMKilled"
    assert len(state.evidence) == 3
    assert state.evidence[2].severity is Severity.CRITICAL


def test_v1_fixture_loads_and_fills_v2_defaults(fixtures_dir: Path) -> None:
    """A v1 checkpoint must load under v2 code (additive fields default to empty)."""
    blob = _load_fixture(fixtures_dir, "v1_sample.json")
    state = load_checkpoint(blob)

    assert state.schema_version == CURRENT_SCHEMA_VERSION  # migrated/stamped to current
    assert state.memory_context == []  # additive v2 field defaults
    assert state.timeline == []
    assert state.total_tokens_used == 0


def test_v2_fixture_loads_under_current_schema(fixtures_dir: Path) -> None:
    """The v2 fixture-replay guarantee: a v2 checkpoint loads with its new fields intact.

    Under v3 code the v2 blob is migrated (version-stamped) to current; the v2
    payload survives and the additive v3 fields fill from their defaults.
    """
    blob = _load_fixture(fixtures_dir, "v2_sample.json")
    state = load_checkpoint(blob)

    assert state.schema_version == CURRENT_SCHEMA_VERSION  # migrated/stamped to current
    assert state.rca is not None
    assert state.rca.root_cause_category == "LatencyRegression"
    assert len(state.memory_context) == 1
    assert state.memory_context[0].similarity == pytest.approx(0.91)
    assert state.memory_context[0].root_cause_category == "LatencyRegression"
    assert len(state.timeline) == 3
    assert state.timeline[0].label == "deploy_started"
    assert state.timeline[1].severity is Severity.ERROR
    assert state.total_tokens_used == 1800
    # Additive v3 fields default to empty when loading an older checkpoint.
    assert state.critique is None
    assert state.knowledge_context == []
    assert state.calibrated_confidence is None
    assert state.prompt_versions == {}


def test_v3_fixture_loads_under_current_schema(fixtures_dir: Path) -> None:
    """The v3 fixture-replay guarantee: a v3 checkpoint loads with its new fields intact."""
    blob = _load_fixture(fixtures_dir, "v3_sample.json")
    state = load_checkpoint(blob)

    assert state.schema_version == CURRENT_SCHEMA_VERSION
    # v2 payload still present.
    assert state.rca is not None
    assert len(state.timeline) == 3
    # v3 additive fields carry their persisted values.
    assert state.critique is not None
    assert state.critique.agreement == pytest.approx(0.9)
    assert state.critique.adjusted_confidence == pytest.approx(0.85)
    assert state.critique.escalate_to_human is False
    assert len(state.knowledge_context) == 1
    assert state.knowledge_context[0].service == "checkout-service"
    assert state.knowledge_context[0].dependencies == ["payments-db", "inventory-service"]
    assert state.calibrated_confidence == pytest.approx(0.85)
    assert state.prompt_versions == {"rca": "v2", "critic": "v1"}


def test_roundtrip_preserves_state(fixtures_dir: Path) -> None:
    blob = _load_fixture(fixtures_dir, "v1_sample.json")
    state = load_checkpoint(blob)
    redumped = dump_checkpoint(state)
    state2 = load_checkpoint(redumped)
    assert state == state2


def test_missing_migration_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blob from a *future* (unknown) version should be rejected cleanly."""
    from kubepilot_orch import state as state_mod

    monkeypatch.setattr(state_mod, "CURRENT_SCHEMA_VERSION", 5)
    monkeypatch.setattr(state_mod, "MIGRATIONS", {})

    with pytest.raises(CheckpointMigrationError) as excinfo:
        state_mod.load_checkpoint({"schema_version": 1, "incident_id": "x"})

    assert excinfo.value.from_ == 1
    assert excinfo.value.to == 5
    assert excinfo.value.missing_step == 1


def test_additive_change_compatibility(fixtures_dir: Path) -> None:
    """Simulate an old checkpoint missing a field added after the fact.

    This documents the additive-only contract: removing/renaming the field below
    would break this test (which is the desired behavior).
    """
    blob = _load_fixture(fixtures_dir, "v1_sample.json")

    # remove an optional field that an even-older checkpoint might not have had
    blob.pop("service", None)
    blob.pop("time_window_minutes", None)

    state = load_checkpoint(blob)
    assert state.service is None  # default
    assert state.time_window_minutes == 30  # default


def test_migration_registry_is_complete() -> None:
    """If we ever bump CURRENT_SCHEMA_VERSION above 1, a migration must exist
    for every step in between.
    """
    from kubepilot_orch.state import MIGRATIONS

    for v in range(1, CURRENT_SCHEMA_VERSION):
        assert v in MIGRATIONS, (
            f"Missing migration v{v}->v{v + 1} after bumping CURRENT_SCHEMA_VERSION "
            f"to {CURRENT_SCHEMA_VERSION}. See docs/ARCHITECTURE.md §3.2.1."
        )
