"""mcp-loki test fixtures — mock the Loki HTTP layer."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest


@dataclass
class FakeLoki:
    response: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def set_response(self, response: Any) -> None:
        self.response = response

    def stage_lines(self, lines: list[tuple[int, str, dict[str, str]]]) -> None:
        """Convenience: stage a Loki query_range response from (epoch_ns, line, labels) tuples.

        Lines with identical labels are bundled into one stream (Loki's wire format).
        """
        streams_by_labels: dict[tuple, dict[str, Any]] = {}
        for ns, line, labels in lines:
            key = tuple(sorted(labels.items()))
            bucket = streams_by_labels.setdefault(key, {"stream": labels, "values": []})
            bucket["values"].append([str(ns), line])
        self.response = {
            "status": "success",
            "data": {"resultType": "streams", "result": list(streams_by_labels.values())},
        }

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"path": path, "params": params or {}})
        return self.response  # type: ignore[no-any-return]


@pytest.fixture
def loki() -> Iterator[FakeLoki]:
    fake = FakeLoki()
    with patch("mcp_loki.client.get", side_effect=fake.get):
        yield fake
