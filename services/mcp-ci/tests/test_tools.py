"""Tests for the three CI tools against an injected fake backend."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mcp_ci.models import (
    Commit,
    CommitList,
    Deployment,
    DeploymentHistory,
    PipelineStatus,
)
from mcp_ci.tools.commits import get_recent_commits
from mcp_ci.tools.deployments import get_deployment_history
from mcp_ci.tools.pipelines import get_pipeline_status


@pytest.mark.asyncio
async def test_get_deployment_history(backend) -> None:  # type: ignore[no-untyped-def]
    backend.deployments = DeploymentHistory(
        service="payment-service",
        window_minutes=30,
        deployments=[
            Deployment(
                service="payment-service",
                version="v1.24.8",
                deployed_at=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
                status="succeeded",
                source="github_actions",
            )
        ],
    )

    result = await get_deployment_history("payment-service", window_minutes=30)

    assert result.service == "payment-service"
    assert len(result.deployments) == 1
    assert result.deployments[0].version == "v1.24.8"
    assert result.deployments[0].deployed_at.tzinfo is not None
    assert backend.calls[0] == {
        "method": "deployment_history",
        "service": "payment-service",
        "window_minutes": 30,
    }


@pytest.mark.asyncio
async def test_get_deployment_history_defaults_window(backend) -> None:  # type: ignore[no-untyped-def]
    backend.deployments = DeploymentHistory(service="web", window_minutes=60, deployments=[])

    await get_deployment_history("web")

    assert backend.calls[0]["window_minutes"] == 60


@pytest.mark.asyncio
async def test_get_recent_commits(backend) -> None:  # type: ignore[no-untyped-def]
    backend.commits = CommitList(
        repo="acme/web",
        window_minutes=60,
        commits=[
            Commit(
                sha="abc123",
                message="fix: null check",
                author="Ada",
                committed_at=datetime(2026, 6, 23, 9, 55, tzinfo=UTC),
                url="https://github.com/acme/web/commit/abc123",
            )
        ],
    )

    result = await get_recent_commits("acme/web")

    assert result.repo == "acme/web"
    assert result.commits[0].sha == "abc123"
    assert result.commits[0].committed_at.tzinfo is not None
    assert backend.calls[0]["method"] == "recent_commits"


@pytest.mark.asyncio
async def test_get_pipeline_status(backend) -> None:  # type: ignore[no-untyped-def]
    backend.pipeline = PipelineStatus(
        repo="acme/web",
        status="failed",
        last_run_at=datetime(2026, 6, 23, 10, 5, tzinfo=UTC),
        url="https://github.com/acme/web/actions/runs/1",
    )

    result = await get_pipeline_status("acme/web")

    assert result.repo == "acme/web"
    assert result.status == "failed"
    assert result.last_run_at.tzinfo is not None
    assert backend.calls[0] == {"method": "pipeline_status", "repo_or_service": "acme/web"}
