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

# MCP server names the harness knows how to wire (must match graph.AgentDeps).
MCP_SERVERS = ("mcp-k8s", "mcp-prom", "mcp-loki")


class Expected(BaseModel):
    """The graded expectations for one scenario (see §7.2)."""

    root_cause_category: str
    min_confidence: float = Field(ge=0.0, le=1.0)
    must_mention_evidence: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    """One hand-authored golden RCA scenario."""

    id: str
    query: str
    namespace: str
    service: str | None = None
    # server_name -> { tool_name -> canned result payload }
    fixture: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expected: Expected

    def server_fixture(self, server_name: str) -> dict[str, Any]:
        """Canned ``{tool: result}`` map for one MCP server (empty if absent)."""
        return self.fixture.get(server_name, {})


def load_scenarios(path: str | Path | None = None) -> list[Scenario]:
    """Read all scenarios from the .jsonl dataset into typed models.

    Blank lines are skipped. Raises on malformed JSON or schema-invalid rows so a
    bad dataset fails loudly rather than silently shrinking the eval set.
    """
    dataset = Path(path) if path is not None else DEFAULT_DATASET
    if not dataset.exists():
        raise FileNotFoundError(f"Golden dataset not found: {dataset}")

    scenarios: list[Scenario] = []
    for lineno, raw in enumerate(dataset.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            blob = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{dataset}:{lineno}: invalid JSON — {e}") from e
        scenarios.append(Scenario.model_validate(blob))

    ids = [s.id for s in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate scenario ids in {dataset}: {sorted(duplicates)}")
    return scenarios
