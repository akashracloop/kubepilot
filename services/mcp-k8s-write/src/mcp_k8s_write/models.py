"""Curated response model for the write server (Phase 4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WriteResult(BaseModel):
    """The outcome of a write-tool invocation.

    In Phase 4 W1 the server is dry-run ONLY: ``dry_run`` is always True and
    ``applied`` is always False. ``preview`` is the human-readable would-be change
    (the real server-side ``dryRun=All`` diff is wired in W7/W11).
    """

    tool: str
    target: str
    namespace: str | None = None
    reversibility: str
    approval_tier: str
    dry_run: bool = True
    applied: bool = False
    preview: str = ""
    note: str | None = None
    warnings: list[str] = Field(default_factory=list)
