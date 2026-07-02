"""Tests for the natural-language request parser."""

from __future__ import annotations

import pytest
from kubepilot_slack.parse import ParsedQuery, parse_request


@pytest.mark.parametrize(
    ("text", "service", "namespace"),
    [
        ("why is payment-service failing in prod?", "payment-service", "prod"),
        ("<@U123ABC> why is checkout-service slow in staging", "checkout-service", "staging"),
        ("investigate api-gateway namespace observability", "api-gateway", "observability"),
        ("what's up with billing-svc ns payments", "billing-svc", "payments"),
        ("@kubepilot look at the order-processor deployment", "order-processor", "prod"),
    ],
)
def test_service_and_namespace_extraction(text: str, service: str, namespace: str) -> None:
    parsed = parse_request(text)
    assert parsed.service == service
    assert parsed.namespace == namespace


def test_defaults_when_nothing_matches() -> None:
    parsed = parse_request("why is everything on fire")
    assert parsed.service is None
    assert parsed.namespace == "prod"


def test_default_namespace_is_configurable() -> None:
    parsed = parse_request("investigate cart-service", default_namespace="dev")
    assert parsed.service == "cart-service"
    assert parsed.namespace == "dev"


def test_mention_is_stripped_from_query() -> None:
    parsed = parse_request("<@U999> why is payment-service failing")
    assert "<@U999>" not in parsed.query
    assert parsed.query.startswith("why is")


def test_bare_in_the_is_not_treated_as_namespace() -> None:
    parsed = parse_request("why is payment-service failing in the last hour")
    assert parsed.namespace == "prod"  # "the" is filtered out
    assert parsed.service == "payment-service"


def test_service_suffix_is_preferred_over_other_hyphenated_tokens() -> None:
    parsed = parse_request("high latency on payment-service via the api-gateway route")
    assert parsed.service == "payment-service"


def test_returns_parsed_query_dataclass() -> None:
    parsed = parse_request("investigate foo-service")
    assert isinstance(parsed, ParsedQuery)
