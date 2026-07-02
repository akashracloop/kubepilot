"""Tempo HTTP API client.

Wraps httpx.AsyncClient with retry and a small surface for the four Phase 2
tools. Connection is single-process-singleton (one AsyncClient per pod).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)


class TempoError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Tempo API error {status}: {body[:200]}")


def _base_url() -> str:
    url = os.getenv("KUBEPILOT_TEMPO_URL", "http://localhost:3200")
    return url.rstrip("/")


def _bearer_token() -> str | None:
    return os.getenv("KUBEPILOT_TEMPO_TOKEN")


@lru_cache(maxsize=1)
def _client() -> httpx.AsyncClient:
    headers = {"Accept": "application/json"}
    token = _bearer_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=_base_url(), headers=headers, timeout=30.0)


def reset_client_cache() -> None:
    """For tests — clears the singleton client."""
    _client.cache_clear()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
    retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
    reraise=True,
)
async def get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    client = _client()
    resp = await client.get(path, params=params or {})
    if resp.status_code >= 400:
        log.error("tempo_http_error", status=resp.status_code, path=path)
        raise TempoError(status=resp.status_code, body=resp.text)

    # Tempo's HTTP API returns bare JSON (no Prometheus-style success envelope),
    # so a 2xx response is the only success signal we validate.
    data = resp.json()
    return data
