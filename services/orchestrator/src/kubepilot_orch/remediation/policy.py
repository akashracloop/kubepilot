"""Execution policy engine (Phase 4 W2) — **default-deny**.

A remediation action executes ONLY if a policy rule explicitly allows it for that
role x action x namespace, within the rule's reversibility tier and blast-radius
caps. No rule matches → **deny** (fail-closed). An empty or missing policy file
means *no action is allowed* — the safest possible default.

Policies are YAML (a ConfigMap in-cluster), validated at load so a malformed file
fails fast rather than at execution time:

    policies:
      - name: restart-only-in-dev
        roles: [operator, admin]
        namespaces: [dev, staging]
        actions: [rollout_restart, restart_pod]
        reversibility: [reversible]
        max_blast_radius: { pods: 10 }

``"*"`` is an explicit opt-in wildcard for ``namespaces`` or ``actions`` — there
are no implicit wildcards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

log = structlog.get_logger(__name__)

WILDCARD = "*"


class BlastRadiusCap(BaseModel):
    """Per-rule maximum impact. None = unbounded for that dimension."""

    pods: int | None = None
    traffic_percent: float | None = None


class PolicyRule(BaseModel):
    """One allow rule. An action is permitted only if it matches a rule fully."""

    name: str
    roles: list[str] = Field(default_factory=list)  # RBAC roles that may execute
    namespaces: list[str] = Field(default_factory=list)  # explicit list or ["*"]
    actions: list[str] = Field(default_factory=list)  # write-tool names or ["*"]
    reversibility: list[str] = Field(default_factory=lambda: ["reversible"])
    max_blast_radius: BlastRadiusCap = Field(default_factory=BlastRadiusCap)

    def _matches_scalar(self, allowed: list[str], value: str) -> bool:
        return WILDCARD in allowed or value in allowed


@dataclass(frozen=True)
class PolicyDecision:
    """The outcome of evaluating one action against the policy set."""

    allowed: bool
    reason: str
    matched_rule: str | None = None


class RemediationPolicy:
    """A set of allow rules, evaluated default-deny."""

    def __init__(self, rules: list[PolicyRule]) -> None:
        self._rules = rules

    @property
    def rules(self) -> list[PolicyRule]:
        return list(self._rules)

    def evaluate(
        self,
        *,
        action: str,
        namespace: str,
        role: str,
        reversibility: str,
        blast_radius_pods: int | None = None,
        blast_radius_traffic: float | None = None,
    ) -> PolicyDecision:
        """Allow the action only if some rule fully matches; otherwise deny."""
        if not self._rules:
            return PolicyDecision(False, "no policy configured — default deny (fail-closed)")

        for rule in self._rules:
            if role not in rule.roles:
                continue
            if not rule._matches_scalar(rule.namespaces, namespace):
                continue
            if not rule._matches_scalar(rule.actions, action):
                continue
            if reversibility not in rule.reversibility:
                continue
            cap = rule.max_blast_radius
            if (
                cap.pods is not None
                and blast_radius_pods is not None
                and blast_radius_pods > cap.pods
            ):
                continue  # over the pod cap — this rule doesn't authorize it
            if (
                cap.traffic_percent is not None
                and blast_radius_traffic is not None
                and blast_radius_traffic > cap.traffic_percent
            ):
                continue
            return PolicyDecision(True, f"allowed by rule '{rule.name}'", rule.name)

        return PolicyDecision(
            False,
            f"no rule allows action={action!r} in namespace={namespace!r} for role={role!r} "
            f"(reversibility={reversibility!r}) — default deny",
        )


def _rules_from_blob(blob: dict[str, Any], *, source: str) -> list[PolicyRule]:
    raw = blob.get("policies", [])
    if not isinstance(raw, list):
        raise ValueError(f"{source}: 'policies' must be a list")
    rules: list[PolicyRule] = []
    for i, entry in enumerate(raw):
        try:
            rules.append(PolicyRule.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"{source}: invalid policy rule #{i}: {e}") from e
    return rules


def load_policies(source: str | Path | None) -> RemediationPolicy:
    """Load policies from a YAML file or a directory of ``*.yaml`` (default-deny).

    ``None`` / missing path / empty content → an empty policy set (denies
    everything), which is the safe default. Malformed YAML raises loudly.
    """
    if source is None:
        return RemediationPolicy([])
    path = Path(source)
    if not path.exists():
        log.warning("remediation_policy_missing", path=str(path))
        return RemediationPolicy([])

    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    rules: list[PolicyRule] = []
    for f in files:
        blob = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if not isinstance(blob, dict):
            raise ValueError(f"{f}: top-level YAML must be a mapping")
        rules.extend(_rules_from_blob(blob, source=str(f)))
    log.info("remediation_policy_loaded", rules=len(rules), files=len(files))
    return RemediationPolicy(rules)


def load_policies_from_yaml(text: str) -> RemediationPolicy:
    """Load a policy set from a YAML string (used by tests / inline config)."""
    blob = yaml.safe_load(text) or {}
    if not isinstance(blob, dict):
        raise ValueError("policy YAML must be a mapping")
    return RemediationPolicy(_rules_from_blob(blob, source="<string>"))


# The packaged reference policies (shipped as a ConfigMap default in-cluster).
_REFERENCE_DIR = Path(__file__).resolve().parent.parent / "policies"


def default_policies() -> RemediationPolicy:
    """Load the shipped reference policy set. Operators override via a ConfigMap."""
    return load_policies(_REFERENCE_DIR)
