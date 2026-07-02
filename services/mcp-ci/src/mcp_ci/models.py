"""Pydantic response models for CI/CD tools.

The backend adapters (GitHub Actions / Jenkins / ArgoCD) each speak a different
wire format; we normalize them into these curated shapes so the agent stays
backend-agnostic. All timestamps are tz-aware UTC.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Deployment(BaseModel):
    """A single deploy of a service."""

    service: str
    version: str
    deployed_at: datetime
    status: str  # "succeeded" | "failed" | "in_progress"
    source: str  # backend name, e.g. "github_actions" | "jenkins" | "argocd"


class DeploymentHistory(BaseModel):
    service: str
    window_minutes: int
    deployments: list[Deployment] = Field(default_factory=list)


class Commit(BaseModel):
    sha: str
    message: str
    author: str
    committed_at: datetime
    url: str | None = None


class CommitList(BaseModel):
    repo: str
    window_minutes: int
    commits: list[Commit] = Field(default_factory=list)


class PipelineStatus(BaseModel):
    repo: str
    status: str  # "succeeded" | "failed" | "in_progress"
    last_run_at: datetime
    url: str | None = None
