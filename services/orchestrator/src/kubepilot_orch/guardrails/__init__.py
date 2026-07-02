"""Guardrails (Phase 3 W10).

Two layers of defense around the model:
- ``sanitize`` — scrub prompt-injection attempts out of untrusted tool results
  before they are fed back to the model.
- ``policy`` — reject destructive recommendations and enforce the write→approval
  invariant before a recommendation reaches a user.
"""

from __future__ import annotations

from kubepilot_orch.guardrails.policy import (
    EnforcementResult,
    PolicyViolation,
    check_recommendation,
    enforce,
)
from kubepilot_orch.guardrails.sanitize import REDACTION_MARKER, SanitizeResult, scrub

__all__ = [
    "REDACTION_MARKER",
    "EnforcementResult",
    "PolicyViolation",
    "SanitizeResult",
    "check_recommendation",
    "enforce",
    "scrub",
]
