"""Architectural test: search_exceptions MUST detect every supported runtime.

This test locks the workload-agnostic guarantee from docs/ARCHITECTURE.md.
A runtime that silently stops being detected is a regression — agents would
miss real exceptions in those workloads.

Adding a new runtime detector? Add a fixture line + assertion below.
Removing one? You're breaking the workload-agnostic contract — discuss first.
"""

from __future__ import annotations

import pytest
from mcp_loki.runtimes import detect
from mcp_loki.tools.exceptions import search_exceptions

# Realistic exception/stack-trace fixtures per runtime. Hand-curated from real-world logs.
RUNTIME_FIXTURES: dict[str, list[str]] = {
    "java": [
        "java.lang.OutOfMemoryError: Java heap space",
        "    at com.example.service.PaymentService.process(PaymentService.java:42)",
        "Caused by: java.sql.SQLException: Connection refused",
        "org.springframework.dao.DataAccessException: could not execute statement",
    ],
    "python": [
        "Traceback (most recent call last):",
        '  File "/app/server.py", line 87, in handle',
        "    response = process(request)",
        "ValueError: Invalid payment amount",
    ],
    "node": [
        "UnhandledPromiseRejectionWarning: Error: ECONNREFUSED",
        "TypeError: Cannot read properties of undefined (reading 'amount')",
        "ReferenceError: processPayment is not defined",
    ],
    "go": [
        "panic: runtime error: invalid memory address or nil pointer dereference",
        "goroutine 14 [running]:",
        "runtime error: index out of range [5] with length 3",
    ],
    "dotnet": [
        "System.NullReferenceException: Object reference not set to an instance of an object.",
        "System.Data.SqlClient.SqlException: Login failed for user 'app'.",
    ],
    "ruby": [
        "Encountered error (NoMethodError)",
        "    from /app/lib/handler.rb:23:in `process_payment'",
    ],
    "generic": [
        "FATAL: connection to upstream service lost",
        "level=PANIC msg='unrecoverable error'",
    ],
}


@pytest.mark.parametrize("runtime,lines", list(RUNTIME_FIXTURES.items()))
def test_each_runtime_detector_matches_at_least_one_line(runtime: str, lines: list[str]) -> None:
    """Every supported runtime must classify at least one of its representative lines.

    If this test fails for runtime X, the workload-agnostic guarantee is broken for X.
    """
    matched = [line for line in lines if detect(line) is not None]
    assert matched, f"No lines matched for runtime={runtime}. Detector regression."


def test_detectors_classify_to_correct_runtime() -> None:
    """The first-line classification must pick the right runtime."""
    cases = [
        ("java.lang.OutOfMemoryError: Java heap space", "java"),
        ("Traceback (most recent call last):", "python"),
        ("TypeError: Cannot read properties of undefined", "node"),
        ("panic: runtime error: invalid memory address", "go"),
        ("System.NullReferenceException: ...", "dotnet"),
        ("Encountered error (NoMethodError)", "ruby"),
        ("FATAL: connection lost", "generic"),
    ]
    for line, expected in cases:
        result = detect(line)
        assert result is not None, f"Did not detect: {line!r}"
        assert result[0] == expected, f"Expected {expected} for {line!r}, got {result[0]}"


def test_detector_does_not_false_positive_on_info_logs() -> None:
    """Conservative on purpose — a noisy detector is worse than a silent one for RCA."""
    benign_lines = [
        "GET /healthz 200 OK",
        "INFO: startup complete",
        "level=info msg='processing batch'",
        '{"level":"info","msg":"order received"}',
        "Successfully processed payment 42",
        "WARN: queue depth growing",  # warn is not an exception
    ]
    for line in benign_lines:
        assert detect(line) is None, f"False positive on benign line: {line!r}"


def test_class_extractor_pulls_exception_class() -> None:
    """When a runtime detector includes a class extractor, it should produce a class."""
    cases = [
        ("java.lang.OutOfMemoryError: Java heap space", "java", "java.lang.OutOfMemoryError"),
        ("TypeError: Cannot read properties of undefined", "node", "TypeError"),
        ("System.NullReferenceException: ...", "dotnet", "System.NullReferenceException"),
        ("Encountered error (NoMethodError)", "ruby", "NoMethodError"),
    ]
    for line, runtime, expected_class in cases:
        result = detect(line)
        assert result == (runtime, expected_class), f"Got {result} for {line!r}"


@pytest.mark.asyncio
async def test_search_exceptions_aggregates_across_runtimes(loki) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: search_exceptions over a mixed-runtime namespace returns counts per runtime."""
    ts_base = 1_718_710_000_000_000_000
    loki.stage_lines(
        [
            (ts_base + 1, "java.lang.OutOfMemoryError: heap", {"app": "billing"}),
            (ts_base + 2, "Traceback (most recent call last):", {"app": "fraud-detector"}),
            (ts_base + 3, "panic: runtime error: nil pointer", {"app": "gateway"}),
            (
                ts_base + 4,
                "GET /healthz 200 OK",
                {"app": "billing"},
            ),  # benign, must be filtered out
            (ts_base + 5, "TypeError: x is undefined", {"app": "frontend-bff"}),
        ]
    )

    view = await search_exceptions(namespace="prod")

    assert view.total == 4
    assert view.by_runtime == {"java": 1, "python": 1, "go": 1, "node": 1}
    # Each match preserves the originating service via stream_labels.
    apps_seen = {m.stream_labels.get("app") for m in view.matches}
    assert apps_seen == {"billing", "fraud-detector", "gateway", "frontend-bff"}


@pytest.mark.asyncio
async def test_search_exceptions_logql_uses_namespace_and_service(loki) -> None:  # type: ignore[no-untyped-def]
    loki.stage_lines([])
    await search_exceptions(namespace="prod", service="payment-service")
    call = loki.calls[0]
    query = call["params"]["query"]
    assert 'namespace="prod"' in query
    assert 'app="payment-service"' in query
    # The exception filter regex is applied via |~ pipeline.
    assert "|~" in query
