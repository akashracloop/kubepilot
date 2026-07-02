"""search_exceptions — workload-agnostic exception detection across runtimes.

This is the tool that codifies the workload-agnostic guarantee from
docs/ARCHITECTURE.md and the IDEA.md ["Workload Scope"] section.

Detection works in two stages:
  1. Loki server-side filters logs with a unified regex (mcp_loki.runtimes.LOGQL_EXCEPTION_FILTER)
     so we don't ship gigabytes of logs to the agent over HTTP.
  2. Client-side, each matched line is classified by runtime (java/python/node/go/dotnet/ruby/generic)
     and the exception class (when extractable) is surfaced.

Tests in tests/test_runtimes.py cover all six runtimes with realistic stack-trace
fixtures. A runtime that silently stops matching is a regression of the
workload-agnostic guarantee.
"""

from __future__ import annotations

from collections import Counter

from mcp_loki.models import ExceptionMatch, ExceptionsView
from mcp_loki.runtimes import LOGQL_EXCEPTION_FILTER, detect
from mcp_loki.tools.base import Tool, register
from mcp_loki.tools.logs import query_logs


async def search_exceptions(
    namespace: str,
    service: str | None = None,
    window_minutes: int = 15,
    limit: int = 500,
) -> ExceptionsView:
    """Search a namespace (and optionally one service) for exception/stack-trace lines.

    Returns a view broken down by detected runtime so the agent can reason about
    *which runtime* is failing, in addition to *what* the exception was.
    """
    if service:
        selector = "{" + f'namespace="{namespace}",app="{service}"' + "}"
    else:
        selector = "{" + f'namespace="{namespace}"' + "}"

    logql = f"{selector} |~ `{LOGQL_EXCEPTION_FILTER}`"

    raw = await query_logs(
        logql=logql,
        window_minutes=window_minutes,
        limit=limit,
    )

    matches: list[ExceptionMatch] = []
    runtime_counter: Counter[str] = Counter()

    for line in raw.lines:
        detected = detect(line.line)
        if detected is None:
            continue
        runtime, exception_class = detected
        matches.append(
            ExceptionMatch(
                timestamp=line.timestamp,
                line=line.line,
                runtime=runtime,
                exception_class=exception_class,
                stream_labels=line.stream_labels,
            )
        )
        runtime_counter[runtime] += 1

    return ExceptionsView(
        query=logql,
        total=len(matches),
        by_runtime=dict(runtime_counter),
        matches=matches,
    )


_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string"},
        "service": {
            "type": ["string", "null"],
            "description": "Optional service / app label to constrain results",
        },
        "window_minutes": {"type": "integer", "minimum": 1, "maximum": 1440, "default": 15},
        "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
    },
    "required": ["namespace"],
    "additionalProperties": False,
}


register(
    Tool(
        name="search_exceptions",
        description=(
            "Find exception / stack-trace lines across ANY runtime (Java, Python, Node, Go, .NET, "
            "Ruby, generic). Returns matches grouped by runtime and exception class. This is the "
            "workload-agnostic primitive — prefer this over runtime-specific LogQL queries."
        ),
        parameters=_SCHEMA,
        handler=search_exceptions,
    )
)
