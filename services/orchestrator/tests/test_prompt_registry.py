"""Tests for the versioned prompt registry (Phase 3 W1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from kubepilot_orch.agents.prompt_registry import (
    ABConfig,
    PromptRegistry,
    default_registry,
)


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    """A throwaway prompts dir: a bare-file prompt and an explicitly versioned one."""
    (tmp_path / "rca_agent.md").write_text("baseline rca", encoding="utf-8")
    (tmp_path / "rca_agent.v2.md").write_text("rca v2", encoding="utf-8")
    (tmp_path / "critic_agent.v1.md").write_text("critic v1", encoding="utf-8")
    (tmp_path / "critic_agent.v2.md").write_text("critic v2", encoding="utf-8")
    return tmp_path


def test_bare_file_resolves_as_v1(prompts_dir: Path) -> None:
    reg = PromptRegistry(prompts_dir=prompts_dir)
    version, text = reg.resolve("rca_agent", "v1")
    assert version == "v1"
    assert text == "baseline rca"


def test_versions_lists_all_ascending(prompts_dir: Path) -> None:
    reg = PromptRegistry(prompts_dir=prompts_dir)
    assert reg.versions("rca_agent") == ["v1", "v2"]
    assert reg.versions("critic_agent") == ["v1", "v2"]


def test_active_version_defaults_to_latest(prompts_dir: Path) -> None:
    reg = PromptRegistry(prompts_dir=prompts_dir)
    assert reg.active_version("rca_agent") == "v2"
    assert reg.render("rca_agent") == "rca v2"


def test_active_version_pin_overrides_latest(prompts_dir: Path) -> None:
    reg = PromptRegistry(prompts_dir=prompts_dir, active={"rca_agent": "v1"})
    assert reg.active_version("rca_agent") == "v1"
    assert reg.render("rca_agent") == "baseline rca"


def test_pin_to_missing_version_raises(prompts_dir: Path) -> None:
    reg = PromptRegistry(prompts_dir=prompts_dir, active={"rca_agent": "v9"})
    with pytest.raises(FileNotFoundError):
        reg.active_version("rca_agent")


def test_resolve_unknown_name_raises(prompts_dir: Path) -> None:
    reg = PromptRegistry(prompts_dir=prompts_dir)
    with pytest.raises(FileNotFoundError):
        reg.versions("nope")


def test_explicit_v1_wins_over_bare(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text("bare", encoding="utf-8")
    (tmp_path / "x.v1.md").write_text("explicit v1", encoding="utf-8")
    reg = PromptRegistry(prompts_dir=tmp_path)
    assert reg.render("x", "v1") == "explicit v1"


def test_select_ab_is_deterministic_and_splits(prompts_dir: Path) -> None:
    reg = PromptRegistry(
        prompts_dir=prompts_dir,
        ab={"critic_agent": ABConfig(a="v1", b="v2", fraction=0.5)},
    )
    # Deterministic: same key → same arm across calls.
    first = reg.select_ab("critic_agent", "incident-abc")
    assert first == reg.select_ab("critic_agent", "incident-abc")
    assert first in {"v1", "v2"}

    # Across many keys both arms are exercised (not all-one-side).
    arms = {reg.select_ab("critic_agent", f"incident-{i}") for i in range(50)}
    assert arms == {"v1", "v2"}


def test_select_ab_without_config_uses_active(prompts_dir: Path) -> None:
    reg = PromptRegistry(prompts_dir=prompts_dir)
    assert reg.select_ab("rca_agent", "any-key") == "v2"


def test_default_registry_sees_packaged_prompts() -> None:
    reg = default_registry()
    # The packaged flat prompts resolve as v1.
    version, text = reg.resolve("rca_agent", "v1")
    assert version == "v1"
    assert text.strip()
