"""Tests for query_logs (LogQL pass-through + stream flattening)."""

from __future__ import annotations

import pytest
from mcp_loki.tools.logs import query_logs


@pytest.mark.asyncio
async def test_query_logs_flattens_streams_newest_first(loki) -> None:  # type: ignore[no-untyped-def]
    loki.stage_lines(
        [
            (1_718_710_000_000_000_000, "earlier line", {"app": "x"}),
            (1_718_710_060_000_000_000, "later line", {"app": "x"}),
            (1_718_710_030_000_000_000, "middle from other stream", {"app": "y"}),
        ]
    )

    result = await query_logs('{namespace="prod"}')

    assert result.total_lines == 3
    assert result.lines[0].line == "later line"
    assert result.lines[-1].line == "earlier line"
    # Stream labels are preserved per line.
    assert result.lines[1].stream_labels == {"app": "y"}


@pytest.mark.asyncio
async def test_query_logs_truncated_when_at_limit(loki) -> None:  # type: ignore[no-untyped-def]
    loki.stage_lines(
        [(1_718_710_000_000_000_000 + i, f"line-{i}", {"app": "x"}) for i in range(10)]
    )
    result = await query_logs('{namespace="prod"}', limit=10)
    assert result.truncated is True


@pytest.mark.asyncio
async def test_query_logs_sends_nanoseconds(loki) -> None:  # type: ignore[no-untyped-def]
    loki.stage_lines([])
    await query_logs('{namespace="prod"}', window_minutes=5)
    call = loki.calls[0]
    assert call["path"] == "/loki/api/v1/query_range"
    # Loki accepts nanoseconds as strings.
    assert call["params"]["start"].isdigit()
    assert call["params"]["end"].isdigit()
    assert int(call["params"]["end"]) > int(call["params"]["start"])
