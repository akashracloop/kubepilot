"""CI/CD backend adapters + factory.

One tool surface, three adapters behind it (GitHub Actions / Jenkins / ArgoCD).
The backend is selected by config (``KUBEPILOT_CI_BACKEND``) and normalized into
the curated models in ``models.py`` so the agent stays backend-agnostic.

Like mcp-prom's ``get_client``, the configured backend is a single-process
singleton (one httpx.AsyncClient per pod), built lazily via ``get_backend``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from mcp_ci.models import (
    Commit,
    CommitList,
    Deployment,
    DeploymentHistory,
    PipelineStatus,
)

log = structlog.get_logger(__name__)

# Curated status vocabulary shared by deployments + pipelines.
_SUCCEEDED = "succeeded"
_FAILED = "failed"
_IN_PROGRESS = "in_progress"

_DEFAULT_BASE_URLS = {
    "github_actions": "https://api.github.com",
    "jenkins": "http://localhost:8080",
    "argocd": "http://localhost:8080",
}


class CIError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"CI API error {status}: {body[:200]}")


class CIBackend(Protocol):
    """Read-only contract every CI adapter implements."""

    name: str

    async def deployment_history(self, service: str, window_minutes: int) -> DeploymentHistory: ...

    async def recent_commits(self, repo: str, window_minutes: int) -> CommitList: ...

    async def pipeline_status(self, repo_or_service: str) -> PipelineStatus: ...


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
    retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
    reraise=True,
)
async def _get_json(
    client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None
) -> Any:
    resp = await client.get(path, params=params or {})
    if resp.status_code >= 400:
        log.error("ci_http_error", status=resp.status_code, path=path)
        raise CIError(status=resp.status_code, body=resp.text)
    return resp.json()


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an RFC3339 string into a tz-aware UTC datetime (or None)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _epoch_millis_to_utc(millis: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(millis) / 1000.0, tz=UTC)
    except (TypeError, ValueError):
        return None


def _cutoff(window_minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=window_minutes)


class GitHubActionsBackend:
    """Wraps the GitHub REST API. ``service``/``repo`` are ``owner/name`` slugs."""

    name = "github_actions"

    def __init__(self, base_url: str, token: str | None) -> None:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0)

    async def deployment_history(self, service: str, window_minutes: int) -> DeploymentHistory:
        raw = await _get_json(self._client, f"/repos/{service}/deployments")
        cutoff = _cutoff(window_minutes)
        deployments: list[Deployment] = []
        for item in raw or []:
            created = _parse_iso(item.get("created_at"))
            if created is None or created < cutoff:
                continue
            deployments.append(
                Deployment(
                    service=service,
                    version=item.get("sha") or item.get("ref") or "unknown",
                    deployed_at=created,
                    status=_gha_deploy_status(item.get("state")),
                    source=self.name,
                )
            )
        return DeploymentHistory(
            service=service, window_minutes=window_minutes, deployments=deployments
        )

    async def recent_commits(self, repo: str, window_minutes: int) -> CommitList:
        since = _cutoff(window_minutes).isoformat()
        raw = await _get_json(self._client, f"/repos/{repo}/commits", params={"since": since})
        commits: list[Commit] = []
        for item in raw or []:
            commit = item.get("commit", {}) or {}
            author = commit.get("author", {}) or {}
            committed = _parse_iso(author.get("date"))
            if committed is None:
                continue
            commits.append(
                Commit(
                    sha=item.get("sha", ""),
                    message=commit.get("message", ""),
                    author=author.get("name", ""),
                    committed_at=committed,
                    url=item.get("html_url"),
                )
            )
        return CommitList(repo=repo, window_minutes=window_minutes, commits=commits)

    async def pipeline_status(self, repo_or_service: str) -> PipelineStatus:
        raw = await _get_json(
            self._client, f"/repos/{repo_or_service}/actions/runs", params={"per_page": 1}
        )
        runs = (raw or {}).get("workflow_runs", []) or []
        run = runs[0] if runs else {}
        return PipelineStatus(
            repo=repo_or_service,
            status=_gha_run_status(run.get("status"), run.get("conclusion")),
            last_run_at=_parse_iso(run.get("updated_at")) or datetime.now(UTC),
            url=run.get("html_url"),
        )


class JenkinsBackend:
    """Wraps the Jenkins JSON API. ``service``/``repo`` are job names."""

    name = "jenkins"

    def __init__(self, base_url: str, token: str | None) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0)

    async def deployment_history(self, service: str, window_minutes: int) -> DeploymentHistory:
        raw = await _get_json(
            self._client,
            f"/job/{service}/api/json",
            params={"tree": "builds[number,result,timestamp,url]"},
        )
        cutoff = _cutoff(window_minutes)
        deployments: list[Deployment] = []
        for build in (raw or {}).get("builds", []) or []:
            deployed = _epoch_millis_to_utc(build.get("timestamp"))
            if deployed is None or deployed < cutoff:
                continue
            deployments.append(
                Deployment(
                    service=service,
                    version=str(build.get("number", "unknown")),
                    deployed_at=deployed,
                    status=_jenkins_status(build.get("result")),
                    source=self.name,
                )
            )
        return DeploymentHistory(
            service=service, window_minutes=window_minutes, deployments=deployments
        )

    async def recent_commits(self, repo: str, window_minutes: int) -> CommitList:
        raw = await _get_json(
            self._client,
            f"/job/{repo}/api/json",
            params={"tree": "builds[timestamp,changeSet[items[commitId,msg,author[fullName]]]]"},
        )
        cutoff = _cutoff(window_minutes)
        commits: list[Commit] = []
        for build in (raw or {}).get("builds", []) or []:
            committed = _epoch_millis_to_utc(build.get("timestamp"))
            if committed is None or committed < cutoff:
                continue
            for item in (build.get("changeSet", {}) or {}).get("items", []) or []:
                author = item.get("author", {}) or {}
                commits.append(
                    Commit(
                        sha=item.get("commitId", ""),
                        message=item.get("msg", ""),
                        author=author.get("fullName", ""),
                        committed_at=committed,
                    )
                )
        return CommitList(repo=repo, window_minutes=window_minutes, commits=commits)

    async def pipeline_status(self, repo_or_service: str) -> PipelineStatus:
        raw = await _get_json(
            self._client,
            f"/job/{repo_or_service}/lastBuild/api/json",
            params={"tree": "result,timestamp,url"},
        )
        raw = raw or {}
        return PipelineStatus(
            repo=repo_or_service,
            status=_jenkins_status(raw.get("result")),
            last_run_at=_epoch_millis_to_utc(raw.get("timestamp")) or datetime.now(UTC),
            url=raw.get("url"),
        )


class ArgoCDBackend:
    """Wraps the ArgoCD REST API. ``service``/``repo`` are application names."""

    name = "argocd"

    def __init__(self, base_url: str, token: str | None) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0)

    async def deployment_history(self, service: str, window_minutes: int) -> DeploymentHistory:
        raw = await _get_json(self._client, f"/api/v1/applications/{service}")
        history = ((raw or {}).get("status", {}) or {}).get("history", []) or []
        cutoff = _cutoff(window_minutes)
        deployments: list[Deployment] = []
        for entry in history:
            deployed = _parse_iso(entry.get("deployedAt"))
            if deployed is None or deployed < cutoff:
                continue
            deployments.append(
                Deployment(
                    service=service,
                    version=entry.get("revision", "unknown"),
                    deployed_at=deployed,
                    status=_argocd_status(entry.get("phase")),
                    source=self.name,
                )
            )
        return DeploymentHistory(
            service=service, window_minutes=window_minutes, deployments=deployments
        )

    async def recent_commits(self, repo: str, window_minutes: int) -> CommitList:
        raw = await _get_json(self._client, f"/api/v1/applications/{repo}")
        history = ((raw or {}).get("status", {}) or {}).get("history", []) or []
        cutoff = _cutoff(window_minutes)
        commits: list[Commit] = []
        for entry in history:
            committed = _parse_iso(entry.get("deployedAt"))
            if committed is None or committed < cutoff:
                continue
            meta = entry.get("source", {}) or {}
            commits.append(
                Commit(
                    sha=entry.get("revision", ""),
                    message=meta.get("targetRevision", ""),
                    author=meta.get("repoURL", ""),
                    committed_at=committed,
                )
            )
        return CommitList(repo=repo, window_minutes=window_minutes, commits=commits)

    async def pipeline_status(self, repo_or_service: str) -> PipelineStatus:
        raw = await _get_json(self._client, f"/api/v1/applications/{repo_or_service}")
        status = (raw or {}).get("status", {}) or {}
        operation = status.get("operationState", {}) or {}
        return PipelineStatus(
            repo=repo_or_service,
            status=_argocd_status(operation.get("phase")),
            last_run_at=_parse_iso(operation.get("finishedAt")) or datetime.now(UTC),
        )


def _gha_deploy_status(state: str | None) -> str:
    mapping = {
        "success": _SUCCEEDED,
        "failure": _FAILED,
        "error": _FAILED,
        "in_progress": _IN_PROGRESS,
        "queued": _IN_PROGRESS,
        "pending": _IN_PROGRESS,
    }
    return mapping.get((state or "").lower(), _IN_PROGRESS)


def _gha_run_status(status: str | None, conclusion: str | None) -> str:
    if (status or "").lower() != "completed":
        return _IN_PROGRESS
    return _SUCCEEDED if (conclusion or "").lower() == "success" else _FAILED


def _jenkins_status(result: str | None) -> str:
    mapping = {
        "SUCCESS": _SUCCEEDED,
        "FAILURE": _FAILED,
        "UNSTABLE": _FAILED,
        "ABORTED": _FAILED,
    }
    return mapping.get((result or "").upper(), _IN_PROGRESS)


def _argocd_status(phase: str | None) -> str:
    mapping = {
        "Succeeded": _SUCCEEDED,
        "Failed": _FAILED,
        "Error": _FAILED,
        "Running": _IN_PROGRESS,
    }
    return mapping.get(phase or "", _IN_PROGRESS)


_BACKENDS: dict[str, type[CIBackend]] = {
    "github_actions": GitHubActionsBackend,
    "jenkins": JenkinsBackend,
    "argocd": ArgoCDBackend,
}


def _backend_name() -> str:
    return os.getenv("KUBEPILOT_CI_BACKEND", "github_actions").strip().lower()


def _base_url(name: str) -> str:
    url = os.getenv("KUBEPILOT_CI_URL") or _DEFAULT_BASE_URLS[name]
    return url.rstrip("/")


def _token() -> str | None:
    return os.getenv("KUBEPILOT_CI_TOKEN")


def make_backend(name: str) -> CIBackend:
    """Construct (but do not cache) the backend for ``name``. Raises on unknown."""
    try:
        cls = _BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown CI backend: {name!r} (expected one of {sorted(_BACKENDS)})"
        ) from None
    return cls(base_url=_base_url(name), token=_token())


@lru_cache(maxsize=1)
def get_backend() -> CIBackend:
    """Return the process-singleton backend selected by ``KUBEPILOT_CI_BACKEND``."""
    return make_backend(_backend_name())


def reset_backend_cache() -> None:
    """For tests — clears the singleton backend."""
    get_backend.cache_clear()
