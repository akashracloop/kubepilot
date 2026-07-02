"""FastAPI entry point.

Phase 1 layout:

  /health, /ready                                   — no auth
  /investigations (POST, GET, GET-by-id, GET stream) — X-API-Key auth

The graph + repo + bus are bound at app construction time and stored on
``app.state``. Tests use ``build_app`` directly to inject in-memory repos
and a scripted graph; production uses ``app`` from this module.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kubepilot_api import __version__
from kubepilot_api.auth import make_principal_dep
from kubepilot_api.config import ApiSettings, load_settings
from kubepilot_api.orchestrator_client import InvestigationOrchestrator
from kubepilot_api.pubsub import InvestigationBus
from kubepilot_api.repository import (
    InMemoryInvestigationRepository,
    InvestigationRepository,
    PostgresInvestigationRepository,
)
from kubepilot_api.routes.health import router as health_router
from kubepilot_api.routes.investigations import make_router as make_investigations_router

log = structlog.get_logger(__name__)


def _default_compiled_graph(
    settings: ApiSettings, checkpointer: Any | None = None, knowledge: Any | None = None
) -> Any:
    """Build the production graph wired to real MCP clients + the configured LLM router.

    Imported lazily so tests can build the app without the langchain dependencies
    when they pass in their own ``compiled_graph``.
    """
    from kubepilot_orch.agents.prompt_registry import default_registry
    from kubepilot_orch.config import load_settings as load_orch_settings
    from kubepilot_orch.graph import AgentDeps, build_graph
    from kubepilot_orch.llm.factory import build_router
    from kubepilot_orch.mcp.adapter import Capability, build_router_from_endpoints

    orch_settings = load_orch_settings()

    # Apply prompt-version pins (the rollback lever) to the shared registry that the
    # reasoning agents resolve against. Config-only + restart → rollback in <5 min.
    if settings.prompt_active_versions:
        default_registry().active.update(settings.prompt_active_versions)

    # Capability-based MCP routing: each domain maps to an endpoint. Endpoints that
    # share a URL share ONE client, so pointing metrics/logs/tracing at a single
    # Grafana MCP URL is a config-only swap (see docs/mcp-adapters.md).
    endpoints: dict[str, str] = {
        Capability.KUBERNETES: settings.mcp.k8s,
        Capability.METRICS: settings.mcp.prom,
        Capability.LOGS: settings.mcp.loki,
    }
    if settings.mcp.tempo:
        endpoints[Capability.TRACING] = settings.mcp.tempo
    if settings.mcp.ci:
        endpoints[Capability.DEPLOYMENT] = settings.mcp.ci
    mcp = build_router_from_endpoints(endpoints)

    deps = AgentDeps(
        llm=build_router(orch_settings),
        mcp_k8s=mcp.client(Capability.KUBERNETES),
        mcp_prom=mcp.client(Capability.METRICS),
        mcp_loki=mcp.client(Capability.LOGS),
        mcp_tempo=mcp.client(Capability.TRACING) if mcp.has(Capability.TRACING) else None,
        mcp_ci=mcp.client(Capability.DEPLOYMENT) if mcp.has(Capability.DEPLOYMENT) else None,
        memory=_build_memory(settings, orch_settings) if settings.memory_enabled else None,
        knowledge=knowledge or (_build_knowledge(settings) if settings.knowledge_enabled else None),
        calibrator=_build_calibrator(settings),
        enable_critic=settings.critic_enabled,
    )
    return build_graph(deps, checkpointer=checkpointer)


def _build_memory(settings: ApiSettings, orch_settings: Any) -> Any:
    """Construct the long-term memory retriever (Phase 2)."""
    from kubepilot_orch.memory import (
        HashEmbedder,
        InMemoryMemoryStore,
        MemoryRetriever,
        OpenAIEmbedder,
    )
    from kubepilot_orch.memory.store import PgVectorMemoryStore

    # BYOK embeddings when an OpenAI key is present; else the offline hash embedder.
    embedder: Any = (
        OpenAIEmbedder(orch_settings.llm.openai_api_key)
        if orch_settings.llm.openai_api_key
        else HashEmbedder()
    )
    store: Any = (
        PgVectorMemoryStore(settings.db.url, embedder.dim)
        if settings.storage == "postgres"
        else InMemoryMemoryStore()
    )
    return MemoryRetriever(embedder, store)


def _build_knowledge(settings: ApiSettings) -> Any:
    """Construct the cluster knowledge-graph retriever (Phase 3).

    The store persists the service graph (owners/deps/SLOs); a separate ingestion
    path (labels / ownership map / ServiceMonitors / dependency discovery) populates
    it. An empty graph simply yields empty knowledge_context — the RCA degrades to
    its Phase-2 behaviour.
    """
    from kubepilot_orch.knowledge import (
        InMemoryKnowledgeStore,
        KnowledgeRetriever,
        PgKnowledgeStore,
    )

    store: Any = (
        PgKnowledgeStore(settings.db.url)
        if settings.storage == "postgres"
        else InMemoryKnowledgeStore()
    )
    return KnowledgeRetriever(store)


def _read_snapshot(path_str: str) -> dict[str, Any] | None:
    """Sync file read (kept out of the async startup path). None if missing/invalid."""
    import json
    from pathlib import Path

    path = Path(path_str)
    if not path.exists():
        log.warning("knowledge_snapshot_missing", path=path_str)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.error("knowledge_snapshot_unreadable", path=path_str, error=str(e))
        return None


async def _build_and_ingest_knowledge(settings: ApiSettings) -> Any:
    """Build the knowledge retriever and, if a snapshot is configured, ingest it.

    Returns None when knowledge is disabled. Ingestion failures are logged, not
    fatal — the RCA degrades to empty knowledge_context rather than failing startup.
    """
    if not settings.knowledge_enabled:
        return None
    retriever = _build_knowledge(settings)
    if settings.knowledge_snapshot_path:
        snapshot = _read_snapshot(settings.knowledge_snapshot_path)
        if snapshot is not None:
            count = await retriever.ingest(snapshot)
            log.info("knowledge_snapshot_ingested", services=count)
    return retriever


def _build_calibrator(settings: ApiSettings) -> Any:
    """Load a trained isotonic calibrator from disk, or None if unset/absent (Phase 3)."""
    import json
    from pathlib import Path

    from kubepilot_orch.calibration import IsotonicCalibrator

    if not settings.calibrator_path:
        return None
    path = Path(settings.calibrator_path)
    if not path.exists():
        log.warning("calibrator_missing", path=str(path))
        return None
    return IsotonicCalibrator.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _default_repository(settings: ApiSettings) -> InvestigationRepository:
    if settings.storage == "memory":
        return InMemoryInvestigationRepository()
    return PostgresInvestigationRepository(url=settings.db.url)


def build_app(
    *,
    settings: ApiSettings | None = None,
    repo: InvestigationRepository | None = None,
    compiled_graph: Any | None = None,
) -> FastAPI:
    """Build a FastAPI app with all dependencies wired.

    Tests pass ``repo`` and ``compiled_graph`` to inject in-memory storage and a
    scripted graph — the orchestrator is bound eagerly at build time.

    Production calls ``build_app()`` with no args: the compiled graph is built
    inside the lifespan so the LangGraph checkpointer (whose Postgres connection
    pool must live exactly as long as the app) is opened at startup and torn down
    at shutdown.
    """
    settings = settings or load_settings()
    repo = repo or _default_repository(settings)
    bus = InvestigationBus()

    # Eagerly-injected graph (tests) → bind the orchestrator now. Otherwise defer
    # to the lifespan so the checkpointer lifecycle brackets the app's lifetime.
    orchestrator: InvestigationOrchestrator | None = None
    if compiled_graph is not None:
        orchestrator = InvestigationOrchestrator(compiled_graph=compiled_graph, repo=repo, bus=bus)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api_starting", version=__version__, environment=settings.environment)

        # AgentOps: enable OTel tracing when a Phoenix/OTLP endpoint is configured.
        from kubepilot_orch.checkpointing import open_checkpointer
        from kubepilot_orch.observability import setup_tracing

        setup_tracing("kubepilot-api", os.environ.get("KUBEPILOT_OTEL_EXPORTER_ENDPOINT"))

        # If the graph wasn't injected, build it here under an open checkpointer.
        if orchestrator is None:
            knowledge = await _build_and_ingest_knowledge(settings)
            async with open_checkpointer(settings.checkpointer, settings.db.url) as checkpointer:
                graph = _default_compiled_graph(
                    settings, checkpointer=checkpointer, knowledge=knowledge
                )
                orch = InvestigationOrchestrator(compiled_graph=graph, repo=repo, bus=bus)
                app.state.orchestrator = orch
                try:
                    yield
                finally:
                    log.info("api_stopping")
                    await orch.shutdown()
                    await repo.aclose()
        else:
            try:
                yield
            finally:
                log.info("api_stopping")
                await orchestrator.shutdown()
                await repo.aclose()

    app = FastAPI(
        title="KubePilot AI",
        version=__version__,
        description="Agentic SRE platform for Kubernetes",
        lifespan=_lifespan,
    )
    app.state.repo = repo
    app.state.bus = bus
    app.state.settings = settings
    if orchestrator is not None:
        app.state.orchestrator = orchestrator

    # CORS so the browser-based Web UI (a different origin) can call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(make_investigations_router(principal_dep=make_principal_dep(settings)))

    return app


# For uvicorn, use the factory pattern so we don't crash at import time when
# Postgres/LLM creds aren't configured:
#
#   uvicorn --factory kubepilot_api.main:build_app
#
# Tests call ``build_app(repo=..., compiled_graph=...)`` directly with in-memory deps.
