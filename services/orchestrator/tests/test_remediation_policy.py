"""Execution policy engine — default-deny + policy matrix (Phase 4 W2)."""

from __future__ import annotations

import pytest
from kubepilot_orch.remediation.policy import (
    RemediationPolicy,
    default_policies,
    load_policies,
    load_policies_from_yaml,
)

_POLICY = """
policies:
  - name: dev-restart
    roles: [operator, admin]
    namespaces: [dev, staging]
    actions: [rollout_restart, restart_pod]
    reversibility: [reversible]
    max_blast_radius: { pods: 10 }
  - name: prod-rollback
    roles: [operator, admin]
    namespaces: [prod]
    actions: [rollout_undo, scale]
    reversibility: [reversible]
    max_blast_radius: { pods: 50 }
  - name: configmap-admin
    roles: [admin]
    namespaces: [prod]
    actions: [edit_configmap]
    reversibility: [reversible, partial]
"""


def _p() -> RemediationPolicy:
    return load_policies_from_yaml(_POLICY)


# ---- default-deny ---------------------------------------------------------


def test_empty_policy_denies_everything() -> None:
    empty = RemediationPolicy([])
    d = empty.evaluate(
        action="rollout_undo", namespace="prod", role="admin", reversibility="reversible"
    )
    assert d.allowed is False
    assert "default deny" in d.reason


def test_missing_policy_path_is_empty_deny(tmp_path) -> None:  # type: ignore[no-untyped-def]
    pol = load_policies(tmp_path / "does-not-exist.yaml")
    assert (
        pol.evaluate(
            action="scale", namespace="prod", role="admin", reversibility="reversible"
        ).allowed
        is False
    )


# ---- matrix: role x action x namespace x reversibility x blast-radius ------


def test_allows_matching_action() -> None:
    d = _p().evaluate(
        action="rollout_undo",
        namespace="prod",
        role="operator",
        reversibility="reversible",
        blast_radius_pods=10,
    )
    assert d.allowed is True
    assert d.matched_rule == "prod-rollback"


@pytest.mark.parametrize(
    ("action", "namespace", "role", "reversibility", "why"),
    [
        ("rollout_undo", "prod", "viewer", "reversible", "role not permitted"),
        ("rollout_undo", "dev", "operator", "reversible", "action not allowed in this ns"),
        ("delete_pvc", "prod", "admin", "reversible", "unknown/forbidden action"),
        ("edit_configmap", "prod", "operator", "partial", "configmap is admin-only"),
        ("scale", "prod", "operator", "irreversible", "reversibility not permitted"),
    ],
)
def test_denies_out_of_policy(action, namespace, role, reversibility, why) -> None:  # type: ignore[no-untyped-def]
    d = _p().evaluate(action=action, namespace=namespace, role=role, reversibility=reversibility)
    assert d.allowed is False, why


def test_blast_radius_cap_denies_over_budget() -> None:
    pol = _p()
    ok = pol.evaluate(
        action="scale",
        namespace="prod",
        role="operator",
        reversibility="reversible",
        blast_radius_pods=40,
    )
    assert ok.allowed is True
    over = pol.evaluate(
        action="scale",
        namespace="prod",
        role="operator",
        reversibility="reversible",
        blast_radius_pods=200,
    )
    assert over.allowed is False  # over the 50-pod cap


def test_wildcard_namespace_matches() -> None:
    pol = load_policies_from_yaml(
        "policies:\n  - name: node\n    roles: [operator]\n    namespaces: ['*']\n"
        "    actions: [cordon]\n    reversibility: [reversible]\n"
    )
    assert (
        pol.evaluate(
            action="cordon", namespace="anything", role="operator", reversibility="reversible"
        ).allowed
        is True
    )


# ---- reference policies + validation --------------------------------------


def test_reference_policies_load_and_are_default_deny_off_policy() -> None:
    pol = default_policies()
    assert len(pol.rules) >= 5
    # An action covered by the reference set is allowed…
    assert (
        pol.evaluate(
            action="rollout_undo",
            namespace="prod",
            role="operator",
            reversibility="reversible",
            blast_radius_pods=10,
        ).allowed
        is True
    )
    # …but anything not covered is still denied.
    assert (
        pol.evaluate(
            action="edit_configmap", namespace="prod", role="viewer", reversibility="partial"
        ).allowed
        is False
    )


def test_malformed_policy_fails_loudly() -> None:
    with pytest.raises(ValueError):
        load_policies_from_yaml("policies:\n  - name: bad\n    roles: 'not-a-list'\n")
    with pytest.raises(ValueError):
        load_policies_from_yaml("policies: 'not-a-list'")
