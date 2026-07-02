"""Knowledge-graph ingestion entrypoint (Phase 3).

Populates the cluster knowledge graph from a snapshot JSON file — the seam a
CronJob / controller runs to keep the graph fresh. Writes to Postgres
(``PgKnowledgeStore``) so the api-gateway's read path sees the same graph.

    python -m kubepilot_orch.knowledge.ingest_cli <snapshot.json> [--owner-map <map.json>]

Environment:
- ``KUBEPILOT_DB_URL`` (or ``--db-url``) — Postgres DSN; omit to use an ephemeral
  in-memory store (a dry-run that just reports the counts).

The snapshot is assembled from **read-only** sources (pod/namespace labels, an
ownership file/annotations, ServiceMonitors, and the Tracing dependency map); see
``records_from_snapshot`` for the shape.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import structlog

from kubepilot_orch.knowledge.graph import InMemoryKnowledgeStore, KnowledgeStore, PgKnowledgeStore
from kubepilot_orch.knowledge.ingest import ingest_snapshot

log = structlog.get_logger(__name__)


def _load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def _run(snapshot_path: str, owner_map_path: str | None, db_url: str | None) -> int:
    snapshot = _load_json(snapshot_path)
    owner_map = _load_json(owner_map_path) if owner_map_path else None

    store: KnowledgeStore
    if db_url:
        store = PgKnowledgeStore(db_url)
    else:
        log.warning("knowledge_ingest_dry_run", reason="no db url; using in-memory store")
        store = InMemoryKnowledgeStore()

    count = await ingest_snapshot(store, snapshot, owner_map=owner_map)
    if isinstance(store, PgKnowledgeStore):
        await store.aclose()
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kubepilot-knowledge-ingest")
    parser.add_argument("snapshot", help="Path to the cluster snapshot JSON.")
    parser.add_argument("--owner-map", default=None, help="Optional owner-map JSON (service→team).")
    parser.add_argument(
        "--db-url",
        default=os.getenv("KUBEPILOT_DB_URL"),
        help="Postgres DSN (default $KUBEPILOT_DB_URL). Omit for an in-memory dry run.",
    )
    args = parser.parse_args(argv)

    count = asyncio.run(_run(args.snapshot, args.owner_map, args.db_url))
    print(f"knowledge graph: ingested {count} services")
    return 0


if __name__ == "__main__":
    sys.exit(main())
