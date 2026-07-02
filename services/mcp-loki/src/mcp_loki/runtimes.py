"""Workload-agnostic exception detection.

This module encodes the architectural commitment from docs/ARCHITECTURE.md
(workload-agnostic). It detects exception/stack-trace patterns across the
common runtimes that ship in Kubernetes clusters:

    Java, Python, Node.js, Go, .NET, Ruby, generic

Patterns are intentionally conservative — we'd rather miss a rare framework
shape than match an info-level log line. False positives confuse RCA agents
worse than false negatives do.

Tests in tests/test_runtimes.py MUST cover all runtimes; a runtime that
silently stops matching is a regression of the workload-agnostic guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern


@dataclass(frozen=True)
class RuntimeDetector:
    runtime: str
    # Pattern that confirms a line is the *start* of an exception/stack-trace.
    primary: Pattern[str]
    # Optional class-name extractor for richer agent reasoning.
    class_extractor: Pattern[str] | None = None


# Detector order matters: specific (Python/Go/Node/.NET/Ruby) before general (Java)
# before fallback (generic). This is what stops `TypeError` (Node) and
# `System.NullReferenceException` (.NET) from matching Java's namespaced-exception
# regex first.
_DETECTORS: tuple[RuntimeDetector, ...] = (
    # Python: traceback marker is unambiguous.
    RuntimeDetector(
        runtime="python",
        primary=re.compile(r"Traceback \(most recent call last\):"),
        class_extractor=re.compile(r"^([A-Z][A-Za-z]*(?:Error|Exception)):", re.MULTILINE),
    ),
    # Go: panic + goroutine markers, plus runtime error.
    RuntimeDetector(
        runtime="go",
        primary=re.compile(r"^panic:|^goroutine \d+ \[|^runtime error:"),
        class_extractor=re.compile(r"^(?:panic:|runtime error:)\s*(.+)$"),
    ),
    # Node.js: unhandled rejections + specific JS error class names (no dots).
    RuntimeDetector(
        runtime="node",
        primary=re.compile(
            r"(?:UnhandledPromiseRejection|"
            r"\b(?:TypeError|ReferenceError|SyntaxError|RangeError|EvalError|URIError):\s)"
        ),
        class_extractor=re.compile(
            r"\b(TypeError|ReferenceError|SyntaxError|RangeError|EvalError|URIError):"
        ),
    ),
    # .NET: namespace-qualified Exception, namespace always starts with System / Microsoft.
    RuntimeDetector(
        runtime="dotnet",
        primary=re.compile(r"\b(?:System|Microsoft)\.(?:\w+\.)*\w+Exception:"),
        class_extractor=re.compile(r"((?:System|Microsoft)\.(?:\w+\.)*\w+Exception)"),
    ),
    # Ruby: "(SomeError)" suffix or "from .../foo.rb:NN:in" frame.
    RuntimeDetector(
        runtime="ruby",
        primary=re.compile(r"\([A-Z][A-Za-z]*Error\)|from .+\.rb:\d+:in"),
        class_extractor=re.compile(r"\(([A-Z][A-Za-z]*Error)\)"),
    ),
    # Java: dotted Exception/Error class names, plus "Caused by:" marker.
    # Requires a dot so bare `TypeError` (Node) does not match here.
    RuntimeDetector(
        runtime="java",
        primary=re.compile(r"(?:^|\s)(?:Caused by:|\w+(?:\.\w+)+(?:Exception|Error))\b"),
        class_extractor=re.compile(r"((?:\w+\.)+(?:\w+Exception|\w+Error))"),
    ),
    # Generic fallback: severe-severity keywords. Catches structured "level=FATAL" logs etc.
    RuntimeDetector(
        runtime="generic",
        primary=re.compile(
            r"(?<![A-Za-z])(?:FATAL|PANIC|CRITICAL|UNCAUGHT(?:_EXCEPTION)?|FAILED_PRECONDITION)\b"
        ),
    ),
)


def detect(line: str) -> tuple[str, str | None] | None:
    """Return (runtime, exception_class) if `line` looks like an exception,
    or None if it doesn't match any detector.

    Detectors are tried in order; the first match wins. The class extractor is
    optional and is used to enrich the agent's view of the failure.
    """
    for det in _DETECTORS:
        if not det.primary.search(line):
            continue
        cls = None
        if det.class_extractor:
            m = det.class_extractor.search(line)
            if m:
                cls = m.group(1)
        return (det.runtime, cls)
    return None


# The LogQL regex we ask Loki to apply server-side, so we don't ship gigabytes of
# logs across the network. Union of *primary* patterns; class extraction still
# happens client-side after we receive the matched lines.
#
# Kept conservative on purpose — LogQL's regex engine (RE2) supports the patterns
# below. If a pattern fails to compile in Loki, the union strategy ensures it
# still degrades to per-runtime fallback at the agent layer.
LOGQL_EXCEPTION_FILTER: str = (
    r"(?i)("
    r"Exception:|Caused by:|"
    r"Traceback \(most recent call last\):|"
    r"UnhandledPromiseRejection|TypeError:|ReferenceError:|"
    r"panic:|goroutine [0-9]+ \[|runtime error:|"
    r"System\.[A-Za-z.]*Exception:|"
    r"\([A-Z][A-Za-z]*Error\)|"
    r"FATAL|PANIC|CRITICAL|UNCAUGHT"
    r")"
)
