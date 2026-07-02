"""Thin Datadog API client (tenacity-retried httpx), mirroring mcp-prom.client.

Reads ``DD_API_KEY`` / ``DD_APP_KEY`` and ``DD_SITE`` (default datadoghq.com) from
the environment. Tests inject a mock transport via :func:`set_client`, so no live
Datadog account is needed to exercise the curated mapping.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class DatadogError(Exception):
    """Non-2xx (or transport) error talking to the Datadog API."""

    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Datadog API error {status}: {detail}")


_client: httpx.AsyncClient | None = None


def _site() -> str:
    return os.getenv("DD_SITE", "datadoghq.com")


def _default_client() -> httpx.AsyncClient:
    headers = {
        "DD-API-KEY": os.getenv("DD_API_KEY", ""),
        "DD-APPLICATION-KEY": os.getenv("DD_APP_KEY", ""),
    }
    return httpx.AsyncClient(base_url=f"https://api.{_site()}", headers=headers, timeout=15.0)


def set_client(client: httpx.AsyncClient) -> None:
    """Override the module client (used by tests to inject a mock transport)."""
    global _client
    _client = client


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = _default_client()
    return _client


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, max=2.0),
    reraise=True,
)
async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    resp = await get_client().request(method, path, **kwargs)
    if resp.status_code >= 400:
        raise DatadogError(resp.status_code, resp.text[:200])
    return resp.json()


async def get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return await _request("GET", path, params=params)


async def post(path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
    return await _request("POST", path, json=json)
