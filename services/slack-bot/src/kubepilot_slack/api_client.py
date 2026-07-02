"""Async HTTP client for the KubePilot API gateway.

Thin wrapper over ``httpx.AsyncClient`` covering the two endpoints the Slack bot
needs: ``POST /investigations`` to kick one off and ``GET /investigations/{id}``
to read the snapshot. Auth is a single ``X-API-Key`` header. This is the seam
tests mock via ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

log = structlog.get_logger(__name__)

# Terminal statuses reported by the gateway (see InvestigationDetail.status).
TERMINAL_STATUSES = frozenset({"completed", "failed"})


class InvestigationApiClient:
    """Async client wrapping the gateway's investigation endpoints."""

    def __init__(
        self,
        api_url: str,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers=headers,
            transport=transport,
            timeout=timeout,
        )

    async def start_investigation(
        self,
        query: str,
        namespace: str,
        service: str | None = None,
    ) -> str:
        """POST a new investigation and return its incident id."""
        payload: dict[str, object] = {"query": query, "namespace": namespace}
        if service:
            payload["service"] = service
        resp = await self._client.post("/investigations", json=payload)
        resp.raise_for_status()
        incident_id = str(resp.json()["incident_id"])
        log.info("investigation_started", incident_id=incident_id, namespace=namespace)
        return incident_id

    async def get_investigation(self, incident_id: str) -> dict:
        """GET the full snapshot for an investigation."""
        resp = await self._client.get(f"/investigations/{incident_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def wait_for(
        self,
        incident_id: str,
        timeout: float = 300.0,  # noqa: ASYNC109 — public signature is spec'd as (id, timeout)
        poll_interval: float = 2.0,
    ) -> dict:
        """Poll ``GET /investigations/{id}`` until the status is terminal.

        Raises :class:`TimeoutError` if the investigation does not reach a
        ``completed``/``failed`` state within ``timeout`` seconds.
        """
        async with asyncio.timeout(timeout):
            while True:
                detail = await self.get_investigation(incident_id)
                if detail.get("status") in TERMINAL_STATUSES:
                    return detail
                await asyncio.sleep(poll_interval)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> InvestigationApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
