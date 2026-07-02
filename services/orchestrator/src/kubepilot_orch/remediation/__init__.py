"""Remediation subsystem (Phase 4) — the gated path from RCA to an executed fix.

Every write flows through: **policy check → blast-radius gate → HITL approval →
execution → audit → auto-rollback watch → post-validation**. This package is
inert unless an operator has enabled remediation; nothing here writes to a cluster
on its own.

- ``policy`` — the default-deny execution policy engine (W2).
"""

from __future__ import annotations

from kubepilot_orch.remediation.policy import (
    PolicyDecision,
    PolicyRule,
    RemediationPolicy,
    load_policies,
)

__all__ = [
    "PolicyDecision",
    "PolicyRule",
    "RemediationPolicy",
    "load_policies",
]
