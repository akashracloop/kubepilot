"""mcp-tempo test fixtures — mock the Tempo HTTP layer."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest


@dataclass
class FakeTempo:
    """Helper passed to tests so they can stage upstream responses and assert calls."""

    response: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def set_response(self, response: Any) -> None:
        self.response = response

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"path": path, "params": params or {}})
        return self.response  # type: ignore[no-any-return]


@pytest.fixture
def tempo() -> Iterator[FakeTempo]:
    fake = FakeTempo()
    with patch("mcp_tempo.client.get", side_effect=fake.get):
        yield fake
