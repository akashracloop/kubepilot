"""Persistence for UI-editable settings overrides (Phase: UI config).

A single JSON document of ``{catalog_key: value}`` overrides. In-memory for
dev/tests; a one-row JSONB table for Postgres. The gateway loads it at startup
(so changes survive restarts) and rewrites it on every admin edit.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)


class SettingsStore(Protocol):
    async def load(self) -> dict[str, Any]: ...
    async def save(self, overrides: dict[str, Any]) -> None: ...
    async def aclose(self) -> None: ...


class InMemorySettingsStore(SettingsStore):
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial or {})

    async def load(self) -> dict[str, Any]:
        return dict(self._data)

    async def save(self, overrides: dict[str, Any]) -> None:
        self._data = dict(overrides)

    async def aclose(self) -> None:
        return


_DDL = """
CREATE TABLE IF NOT EXISTS app_settings (
    id         INT PRIMARY KEY DEFAULT 1,
    data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT app_settings_singleton CHECK (id = 1)
);
"""


class PostgresSettingsStore(SettingsStore):
    """One-row JSONB store, sharing the app database."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._pool: Any = None

    async def _ensure(self) -> Any:
        if self._pool is None:
            import asyncpg  # lazy: only when Postgres storage is selected

            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=2)
            async with self._pool.acquire() as conn:
                await conn.execute(_DDL)
        return self._pool

    async def load(self) -> dict[str, Any]:
        import json

        pool = await self._ensure()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT data FROM app_settings WHERE id = 1")
        if row is None or row["data"] is None:
            return {}
        data = row["data"]
        return json.loads(data) if isinstance(data, str) else dict(data)

    async def save(self, overrides: dict[str, Any]) -> None:
        import json

        pool = await self._ensure()
        payload = json.dumps(overrides)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (id, data, updated_at)
                VALUES (1, $1::jsonb, now())
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                """,
                payload,
            )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def build_settings_store(storage: str, db_url: str) -> SettingsStore:
    return PostgresSettingsStore(db_url) if storage == "postgres" else InMemorySettingsStore()
