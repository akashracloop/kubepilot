"""Load golden RCA scenarios from the newline-delimited JSON dataset.

Each line of ``eval/datasets/golden_rca_scenarios.jsonl`` is one scenario:

    {
      "id": "java-spring-oom-001",
      "query": "why is payment-service failing?",
      "namespace": "prod",
      "service": "payment-service",
      "fixture": { "mcp-k8s": {...}, "mcp-prom": {...}, "mcp-loki": {...} },
      "expected": { "root_cause_category": "OOMKilled", "min_confidence": 0.7,
                    "must_mention_evidence": ["memory", "restart", "137"] }
    }

``fixture`` maps an MCP server name to a ``{tool_name: canned_result}`` dict. The
runner serves those canned results through an ``httpx.MockTransport`` so no real
MCP server or cluster is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Repo-relative default location of the golden dataset.
DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "datasets" / "golden_rca_scenarios.jsonl"

# Held-out set — distinct scenarios scored separately from golden to detect
# overfitting to the golden prompts (Phase 3 §7).
HELDOUT_DATASET = (
    Path(__file__).resolve().parent.parent / "datasets" / "heldout_rca_scenarios.jsonl"
)

# MCP server names the harness knows how to wire (must match graph.AgentDeps).
MCP_SERVERS = ("mcp-k8s", "mcp-prom", "mcp-loki")


class Expected(BaseModel):
    """The graded expectations for one scenario (see §7.2)."""

    root_cause_category: str
    min_confidence: float = Field(ge=0.0, le=1.0)
    must_mention_evidence: list[str] = Field(default_factory=list)


class MemorySeed(BaseModel):
    """A prior incident to pre-seed into long-term memory before an investigation.

    The runner indexes each seed into an in-memory ``MemoryRetriever`` so the
    memory node can recall it — used to exercise recurring-incident scenarios.
    """

    summary: str
    root_cause_category: str | None = None
    namespace: str | None = None
    service: str | None = None
    outcome: str | None = None


class Scenario(BaseModel):
    """One hand-authored golden RCA scenario.

    ``fixture`` accepts arbitrary MCP server keys — the Phase 1 trio
    (``mcp-k8s``/``mcp-prom``/``mcp-loki``) plus the optional Phase 2 servers
    (``mcp-tempo``/``mcp-ci``). ``memory_seed`` (optional) pre-seeds long-term
    memory so recurring-incident scenarios can be graded.
    """

    id: str
    query: str
    namespace: str
    service: str | None = None
    # server_name -> { tool_name -> canned result payload }
    fixture: dict[str, dict[str, Any]] = Field(default_factory=dict)
    memory_seed: list[MemorySeed] = Field(default_factory=list)
    expected: Expected

    def server_fixture(self, server_name: str) -> dict[str, Any]:
        """Canned ``{tool: result}`` map for one MCP server (empty if absent)."""
        return self.fixture.get(server_name, {})


def _load_jsonl(dataset: str | Path) -> list[dict[str, Any]]:
    """Read a newline-delimited JSON file into a list of dict blobs.

    Blank lines are skipped. Raises on a missing file or malformed JSON so a bad
    dataset fails loudly rather than silently shrinking the eval set. Shared by
    the RCA loader and the timeline eval so both parse identically.
    """
    dataset = Path(dataset)
    if not dataset.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset}")

    blobs: list[dict[str, Any]] = []
    for lineno, raw in enumerate(dataset.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            blobs.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{dataset}:{lineno}: invalid JSON — {e}") from e
    return blobs


def load_scenarios(path: str | Path | None = None) -> list[Scenario]:
    """Read all scenarios from the .jsonl dataset into typed models.

    Blank lines are skipped. Raises on malformed JSON or schema-invalid rows so a
    bad dataset fails loudly rather than silently shrinking the eval set.
    """
    dataset = Path(path) if path is not None else DEFAULT_DATASET
    scenarios = [Scenario.model_validate(blob) for blob in _load_jsonl(dataset)]

    ids = [s.id for s in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate scenario ids in {dataset}: {sorted(duplicates)}")
    return scenarios


def load_heldout() -> list[Scenario]:
    """Load the held-out RCA scenarios (distinct from golden; overfit detector)."""
    return load_scenarios(HELDOUT_DATASET)
