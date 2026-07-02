"""LangGraph checkpointer construction.

The checkpointer persists investigation state at every node transition so an
investigation can pause/resume and survive pod restarts (ARCHITECTURE.md §3.2,
§7.1). Two backends:

- ``postgres`` — production. ``AsyncPostgresSaver`` against the same Postgres the
  gateway uses. Imported lazily because it pulls in psycopg/libpq, which dev
  machines without a Postgres client library don't have.
- ``memory`` — dev / tests. In-process; does NOT survive restarts.

Usage (in the gateway lifespan)::

    async with open_checkpointer(settings.checkpointer, settings.db.url) as cp:
        graph = build_graph(deps, checkpointer=cp)
        ...  # serve requests
    # checkpointer connection pool is torn down on exit
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver

log = structlog.get_logger(__name__)

VALID_BACKENDS = ("postgres", "memory")


@asynccontextmanager
async def open_checkpointer(backend: str, db_url: str) -> AsyncIterator[Any]:
    """Yield a LangGraph checkpointer for the given backend.

    Raises ``ValueError`` on an unknown backend so a misconfiguration fails fast
    at startup rather than silently running without persistence.
    """
    if backend == "memory":
        log.info("checkpointer_backend", backend="memory")
        yield MemorySaver()
        return

    if backend == "postgres":
        # Lazy import: psycopg/libpq is only required when Postgres is selected.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        log.info("checkpointer_backend", backend="postgres")
        async with AsyncPostgresSaver.from_conn_string(db_url) as saver:
            await saver.setup()  # idempotent: creates checkpoint tables if absent
            yield saver
        return

    raise ValueError(f"Unknown checkpointer backend {backend!r}; expected one of {VALID_BACKENDS}")
