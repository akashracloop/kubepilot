"""Architectural test: the write ClusterRole grants EXACTLY the tool footprint.

The mcp-k8s-write ServiceAccount is the only write grant in the platform. This
test renders the Helm chart and asserts its ClusterRole equals
``safety.required_rbac()`` — no verb, resource, or apiGroup beyond what the
curated write tools need — and contains no destructive footprint (no delete on
secrets / pvc / pv / namespaces). If a tool changes its RBAC needs, update the
chart; drift fails here.

Skips cleanly if ``helm`` isn't installed (CI installs it).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from mcp_k8s_write.safety import required_rbac

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART_PATH = REPO_ROOT / "charts" / "kubepilot-ai"

# Resources the write role must NEVER touch (destructive / secret-bearing).
FORBIDDEN_RESOURCES = {"secrets", "persistentvolumeclaims", "persistentvolumes", "namespaces"}
FORBIDDEN_VERBS = {"*", "deletecollection", "impersonate", "escalate", "bind"}


def _render_cluster_role() -> dict:  # type: ignore[type-arg]
    if shutil.which("helm") is None:
        pytest.skip("helm CLI not installed")
    helm_args = [
        "helm",
        "template",
        "kubepilot-test",
        str(CHART_PATH),
        "--namespace",
        "kubepilot-system",
        "--set",
        "remediation.enabled=true",
        "--show-only",
        "templates/mcp-k8s-write-rbac.yaml",
    ]
    out = subprocess.run(helm_args, capture_output=True, text=True, check=True)  # noqa: S603
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    roles = [d for d in docs if d.get("kind") == "ClusterRole"]
    assert len(roles) == 1, "expected exactly one write ClusterRole"
    return roles[0]


def _rendered_footprint(role: dict) -> dict[str, set[str]]:  # type: ignore[type-arg]
    footprint: dict[str, set[str]] = {}
    for rule in role.get("rules", []):
        verbs = set(rule.get("verbs", []))
        for group in rule.get("apiGroups", []):
            for resource in rule.get("resources", []):
                footprint.setdefault(f"{group}/{resource}", set()).update(verbs)
    return footprint


def test_write_role_matches_tool_footprint_exactly() -> None:
    role = _render_cluster_role()
    rendered = _rendered_footprint(role)
    expected = required_rbac()
    assert rendered == expected, (
        f"write ClusterRole drifted from the tool footprint.\n"
        f"rendered={rendered}\nexpected={expected}"
    )


def test_write_role_has_no_destructive_footprint() -> None:
    role = _render_cluster_role()
    for rule in role.get("rules", []):
        resources = set(rule.get("resources", []))
        verbs = set(rule.get("verbs", []))
        assert not (resources & FORBIDDEN_RESOURCES), f"forbidden resource in {rule}"
        assert not (verbs & FORBIDDEN_VERBS), f"forbidden verb in {rule}"
