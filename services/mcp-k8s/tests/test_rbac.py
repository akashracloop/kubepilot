"""Architectural test: the k8s-mcp RBAC template MUST be read-only.

Per docs/ARCHITECTURE.md §1 ("Read-only by default") and §8.3 ("read-only by
enforcement, not just by convention"), the ClusterRole/Role granted to the
mcp-k8s ServiceAccount must contain ONLY get/list/watch verbs.

This test parses the Helm template (with helm template rendering) and asserts
that no forbidden verb appears. If you ever need to grant a write verb here,
that's a Phase 4 ticket — discuss before bypassing this test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

# Verbs allowed for the read-only investigator. Anything else fails the test.
ALLOWED_VERBS: frozenset[str] = frozenset({"get", "list", "watch"})

# Verbs that MUST never appear on this role.
FORBIDDEN_VERBS: frozenset[str] = frozenset(
    {
        "create",
        "update",
        "patch",
        "delete",
        "deletecollection",
        "*",
        "impersonate",
        "bind",
        "escalate",
    }
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CHART_PATH = REPO_ROOT / "charts" / "kubepilot-ai"


@pytest.fixture(scope="module")
def rendered_rbac() -> list[dict]:
    """Render the chart with helm and return the parsed RBAC manifests."""
    if shutil.which("helm") is None:
        pytest.skip("helm CLI not installed")

    # helm resolved from PATH — acceptable for a controlled test
    helm_args = [
        "helm",
        "template",
        "kubepilot-test",
        str(CHART_PATH),
        "--namespace",
        "kubepilot-system",
        "--show-only",
        "templates/mcp-k8s-rbac.yaml",
    ]
    proc = subprocess.run(helm_args, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode != 0:
        pytest.fail(f"helm template failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    docs = [d for d in yaml.safe_load_all(proc.stdout) if d is not None]
    return docs


def test_helm_template_renders(rendered_rbac: list[dict]) -> None:
    assert rendered_rbac, "RBAC template produced no manifests"


def test_only_clusterrole_and_binding_emitted_by_default(rendered_rbac: list[dict]) -> None:
    """Default scope is 'cluster' — expect a ClusterRole and a ClusterRoleBinding."""
    kinds = {doc["kind"] for doc in rendered_rbac}
    assert "ClusterRole" in kinds
    assert "ClusterRoleBinding" in kinds


def test_role_contains_no_forbidden_verbs(rendered_rbac: list[dict]) -> None:
    """The architectural read-only guarantee — single most important test in this module."""
    for doc in rendered_rbac:
        if doc.get("kind") not in {"ClusterRole", "Role"}:
            continue
        for rule_idx, rule in enumerate(doc.get("rules") or []):
            verbs = set(rule.get("verbs") or [])
            forbidden = verbs & FORBIDDEN_VERBS
            assert not forbidden, (
                f"{doc['kind']} '{doc['metadata']['name']}' rule[{rule_idx}] "
                f"contains forbidden verbs: {sorted(forbidden)}. "
                f"Read-only contract violated — see docs/ARCHITECTURE.md §8.3."
            )


def test_role_uses_only_allowed_verbs(rendered_rbac: list[dict]) -> None:
    """Stricter check: every verb on the role must be in the allowlist.

    This guards against introduction of new exotic verbs ('proxy', 'use', etc.)
    that aren't explicitly forbidden but also have no business on an
    investigation-only ServiceAccount.
    """
    for doc in rendered_rbac:
        if doc.get("kind") not in {"ClusterRole", "Role"}:
            continue
        for rule_idx, rule in enumerate(doc.get("rules") or []):
            verbs = set(rule.get("verbs") or [])
            extra = verbs - ALLOWED_VERBS
            assert not extra, (
                f"{doc['kind']} '{doc['metadata']['name']}' rule[{rule_idx}] "
                f"contains verbs outside the read-only allowlist: {sorted(extra)}"
            )
