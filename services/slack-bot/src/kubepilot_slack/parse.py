"""Parse a natural-language Slack mention into an investigation request.

The heuristic is intentionally simple and well-tested:

* Strip Slack mention tokens (``<@U123>``) and a leading ``@kubepilot``.
* A token matching a k8s-name pattern (``[a-z0-9-]+`` that contains a hyphen or
  ends in ``-service`` / ``-svc``) is taken as the service name.
* ``in <ns>`` or ``namespace <ns>`` sets the namespace; otherwise the caller's
  default namespace is used.
* The (cleaned) text is passed through as the free-form query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A k8s-style name: lowercase segments joined by hyphens (so it always contains
# at least one hyphen). Matches "payment-service", "checkout-svc", "api-gateway".
_SERVICE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")

# Explicit namespace declarations. "namespace"/"ns" is a strong signal; the bare
# "in <ns>" form is weaker, so it is filtered against common English words below.
_NS_EXPLICIT_RE = re.compile(r"\b(?:namespace|ns)\s+([a-z0-9][a-z0-9-]*)", re.IGNORECASE)
_NS_IN_RE = re.compile(r"\bin\s+([a-z0-9][a-z0-9-]*)", re.IGNORECASE)

# Slack renders a bot mention as "<@U…>"; also tolerate a plain "@kubepilot".
_MENTION_RE = re.compile(r"<@[^>]+>|@\w+")

# Words that follow "in" but are never a namespace — avoids "in the …" etc.
_NS_STOPWORDS = frozenset(
    {"the", "a", "an", "our", "my", "this", "that", "order", "which", "case", "here"}
)


@dataclass(frozen=True)
class ParsedQuery:
    query: str
    service: str | None
    namespace: str


def parse_request(text: str, default_namespace: str = "prod") -> ParsedQuery:
    """Extract a service and namespace from a natural-language request."""
    cleaned = _MENTION_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    namespace = _extract_namespace(cleaned, default_namespace)
    service = _extract_service(cleaned, namespace)

    return ParsedQuery(query=cleaned, service=service, namespace=namespace)


def _extract_namespace(text: str, default_namespace: str) -> str:
    explicit = _NS_EXPLICIT_RE.search(text)
    if explicit:
        return explicit.group(1).lower()

    loose = _NS_IN_RE.search(text)
    if loose:
        candidate = loose.group(1).lower()
        # A hyphenated candidate is more likely a service ("in payment-service");
        # a common English word after "in" is not a namespace either.
        if "-" not in candidate and candidate not in _NS_STOPWORDS:
            return candidate

    return default_namespace


def _extract_service(text: str, namespace: str) -> str | None:
    candidates = [m.group(0).lower() for m in _SERVICE_RE.finditer(text)]
    candidates = [c for c in candidates if c != namespace]
    if not candidates:
        return None

    # Prefer an explicit workload suffix when several tokens qualify.
    for candidate in candidates:
        if candidate.endswith(("-service", "-svc")):
            return candidate
    return candidates[0]
