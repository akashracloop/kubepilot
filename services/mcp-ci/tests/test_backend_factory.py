"""The backend factory selects the right adapter from KUBEPILOT_CI_BACKEND."""

from __future__ import annotations

import pytest
from mcp_ci.client import (
    ArgoCDBackend,
    GitHubActionsBackend,
    JenkinsBackend,
    get_backend,
    make_backend,
    reset_backend_cache,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("github_actions", GitHubActionsBackend),
        ("jenkins", JenkinsBackend),
        ("argocd", ArgoCDBackend),
    ],
)
def test_make_backend_selects_class(name: str, expected: type) -> None:
    assert isinstance(make_backend(name), expected)


def test_make_backend_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown CI backend"):
        make_backend("gitlab")


def test_get_backend_reads_env_and_is_singleton(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KUBEPILOT_CI_BACKEND", "jenkins")
    reset_backend_cache()

    first = get_backend()
    second = get_backend()

    assert isinstance(first, JenkinsBackend)
    assert first is second  # lru_cache singleton
    reset_backend_cache()


def test_get_backend_defaults_to_github_actions(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("KUBEPILOT_CI_BACKEND", raising=False)
    reset_backend_cache()

    assert isinstance(get_backend(), GitHubActionsBackend)
    reset_backend_cache()
