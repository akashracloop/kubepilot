"""Async HTTP client for the KubePilot API gateway.

Thin wrapper over the investigations REST contract:
  POST /investigations              -> create
  GET  /investigations/{id}         -> get
  GET  /investigations?limit&offset -> list

``build_client`` is the seam tests mock: pass an ``httpx.MockTransport`` (and a
``Settings`` with a known ``api_key``) to exercise requests without a network.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from kubepilot_cli.config import Settings, load_config

API_KEY_HEADER = "X-API-Key"
DEFAULT_TIMEOUT = 30.0

# Investigation lifecycle: pending -> running -> completed | failed.
TERMINAL_STATUSES = frozenset({"completed", "failed"})


class ApiError(RuntimeError):
    """Raised when the API returns a non-2xx response or is unreachable."""


def build_client(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Construct an ``AsyncClient`` bound to the configured API URL and key."""
    headers: dict[str, str] = {}
    if settings.api_key:
        headers[API_KEY_HEADER] = settings.api_key
    return httpx.AsyncClient(
        base_url=settings.api_url,
        headers=headers,
        transport=transport,
        timeout=DEFAULT_TIMEOUT,
    )


def _resolve(settings: Settings | None) -> Settings:
    return settings if settings is not None else load_config()


def _handle(response: httpx.Response) -> dict[str, Any]:
    if response.is_success:
        return response.json()  # type: ignore[no-any-return]
    detail = _error_detail(response)
    raise ApiError(f"API request failed ({response.status_code}): {detail}")


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


async def create(
    query: str,
    namespace: str,
    service: str | None,
    time_window_minutes: int,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Start a new investigation. Returns the create response (incident_id, ...)."""
    settings = _resolve(settings)
    payload: dict[str, Any] = {
        "query": query,
        "namespace": namespace,
        "time_window_minutes": time_window_minutes,
    }
    if service is not None:
        payload["service"] = service
    async with build_client(settings, transport=transport) as client:
        try:
            response = await client.post("/investigations", json=payload)
        except httpx.HTTPError as exc:
            raise ApiError(f"Could not reach API at {settings.api_url}: {exc}") from exc
    return _handle(response)


async def get(
    incident_id: str,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fetch a single investigation snapshot."""
    settings = _resolve(settings)
    async with build_client(settings, transport=transport) as client:
        try:
            response = await client.get(f"/investigations/{incident_id}")
        except httpx.HTTPError as exc:
            raise ApiError(f"Could not reach API at {settings.api_url}: {exc}") from exc
    return _handle(response)


async def list(
    limit: int = 20,
    offset: int = 0,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """List investigations with pagination."""
    settings = _resolve(settings)
    async with build_client(settings, transport=transport) as client:
        try:
            response = await client.get(
                "/investigations",
                params={"limit": limit, "offset": offset},
            )
        except httpx.HTTPError as exc:
            raise ApiError(f"Could not reach API at {settings.api_url}: {exc}") from exc
    return _handle(response)


async def wait_for(
    incident_id: str,
    timeout: float,  # noqa: ASYNC109 - explicit poll timeout is part of the CLI contract
    poll: float = 1.0,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Poll ``get`` until the investigation reaches a terminal status or times out."""
    settings = _resolve(settings)
    deadline = time.monotonic() + timeout
    while True:
        detail = await get(incident_id, settings=settings, transport=transport)
        if detail.get("status") in TERMINAL_STATUSES:
            return detail
        if time.monotonic() >= deadline:
            raise ApiError(
                f"Timed out after {timeout:.0f}s waiting for investigation "
                f"{incident_id} (last status: {detail.get('status')!r})"
            )
        await asyncio.sleep(poll)
