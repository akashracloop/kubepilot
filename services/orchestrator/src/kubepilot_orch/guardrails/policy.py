"""Output guardrails for recommendations (Phase 3 W10).

KubePilot is read-only through Phase 3: it must **never suggest a destructive
action**. This module is the last line before a recommendation reaches a user —
it rejects irreversible/destructive commands (delete PVC/PV/namespace/secret,
``rm -rf``, ``--force --grace-period=0``, DB drops, disk wipes, helm uninstall)
and enforces the write→approval invariant as a second layer behind the
recommendation agent's own check.

Rejected recommendations are dropped from the output and returned as
``PolicyViolation``s for AgentOps + the UI, so a blocked suggestion is visible,
not silently missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kubepilot_orch.state import Recommendation

# Irreversible / destructive command signatures that must never be recommended.
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "delete_persistent_data",
        re.compile(r"\bdelete\s+(pvc|persistentvolumeclaim|pv|persistentvolume)s?\b", re.I),
    ),
    ("delete_namespace", re.compile(r"\bdelete\s+(namespace|ns)\b", re.I)),
    ("delete_secret", re.compile(r"\bdelete\s+secrets?\b", re.I)),
    (
        "force_delete",
        re.compile(r"--force\b.*--grace-period[= ]0|--grace-period[= ]0\b.*--force", re.I),
    ),
    ("recursive_remove", re.compile(r"\brm\s+-rf?\b", re.I)),
    ("disk_wipe", re.compile(r"\b(mkfs|dd\s+if=|wipefs)\b", re.I)),
    ("db_drop", re.compile(r"\bdrop\s+(table|database|schema)\b", re.I)),
    ("truncate_table", re.compile(r"\btruncate\s+table\b", re.I)),
    ("helm_uninstall", re.compile(r"\bhelm\s+(uninstall|delete)\b", re.I)),
)

# Write verbs that require approval (defense-in-depth behind the recommendation agent).
_WRITE_VERBS: tuple[str, ...] = (
    " apply ",
    " delete ",
    " create ",
    " patch ",
    " replace ",
    " scale ",
    " rollout ",
    " edit ",
    " drain ",
    " cordon ",
    " uncordon ",
    " evict ",
    " rollback",
    " upgrade ",
)


@dataclass(frozen=True)
class PolicyViolation:
    """A blocked or corrected recommendation, surfaced for the UI/AgentOps."""

    kind: str  # e.g. "delete_namespace", "write_requires_approval"
    title: str  # the offending recommendation's title
    detail: str


def _has_write_command(rec: Recommendation) -> bool:
    for cmd in rec.commands:
        padded = " " + cmd.lower() + " "
        if any(verb in padded for verb in _WRITE_VERBS):
            return True
    return False


def check_recommendation(rec: Recommendation) -> list[PolicyViolation]:
    """Return forbidden-command violations for one recommendation (empty if clean)."""
    violations: list[PolicyViolation] = []
    for cmd in rec.commands:
        for kind, pat in _FORBIDDEN_PATTERNS:
            if pat.search(cmd):
                violations.append(
                    PolicyViolation(kind=kind, title=rec.title, detail=f"forbidden command: {cmd}")
                )
    return violations


@dataclass
class EnforcementResult:
    """Recommendations that passed policy + the violations that were acted on."""

    kept: list[Recommendation] = field(default_factory=list)
    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def blocked_any(self) -> bool:
        return bool(self.violations)


def enforce(recs: list[Recommendation]) -> EnforcementResult:
    """Drop destructive recommendations; force approval on any remaining write.

    A recommendation with a forbidden command is removed entirely (its violations
    recorded). A non-forbidden write command that somehow arrived without
    ``requires_approval`` is corrected in place (recorded as a violation too).
    """
    kept: list[Recommendation] = []
    violations: list[PolicyViolation] = []

    for rec in recs:
        forbidden = check_recommendation(rec)
        if forbidden:
            violations.extend(forbidden)
            continue  # drop the whole recommendation
        if _has_write_command(rec) and not rec.requires_approval:
            violations.append(
                PolicyViolation(
                    kind="write_requires_approval",
                    title=rec.title,
                    detail="write command forced to requires_approval=True",
                )
            )
            rec = rec.model_copy(update={"requires_approval": True})
        kept.append(rec)

    return EnforcementResult(kept=kept, violations=violations)
