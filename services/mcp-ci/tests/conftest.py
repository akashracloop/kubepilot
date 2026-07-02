"""mcp-ci test fixtures — inject a fake CI backend via the singleton override.

Mirrors mcp-prom's approach of patching the client layer, except here the unit
of substitution is the whole pluggable backend rather than a single ``get``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from mcp_ci.models import CommitList, DeploymentHistory, PipelineStatus


@dataclass
class FakeBackend:
    """Stages curated responses and records the calls the tools make."""

    name: str = "fake"
    deployments: DeploymentHistory | None = None
    commits: CommitList | None = None
    pipeline: PipelineStatus | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def deployment_history(self, service: str, window_minutes: int) -> DeploymentHistory:
        self.calls.append(
            {"method": "deployment_history", "service": service, "window_minutes": window_minutes}
        )
        assert self.deployments is not None
        return self.deployments

    async def recent_commits(self, repo: str, window_minutes: int) -> CommitList:
        self.calls.append(
            {"method": "recent_commits", "repo": repo, "window_minutes": window_minutes}
        )
        assert self.commits is not None
        return self.commits

    async def pipeline_status(self, repo_or_service: str) -> PipelineStatus:
        self.calls.append({"method": "pipeline_status", "repo_or_service": repo_or_service})
        assert self.pipeline is not None
        return self.pipeline


@pytest.fixture
def backend() -> Iterator[FakeBackend]:
    fake = FakeBackend()
    with patch("mcp_ci.client.get_backend", return_value=fake):
        yield fake
