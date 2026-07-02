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


def _default_compiled_graph(settings: ApiSettings, checkpointer: Any | None = None) -> Any:
    """Build the production graph wired to real MCP clients + the configured LLM router.

    Imported lazily so tests can build the app without the langchain dependencies
    when they pass in their own ``compiled_graph``.
    """
    from kubepilot_orch.config import load_settings as load_orch_settings
    from kubepilot_orch.graph import AgentDeps, build_graph
    from kubepilot_orch.llm.factory import build_router
    from kubepilot_orch.mcp.adapter import Capability, build_router_from_endpoints

    orch_settings = load_orch_settings()

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
            async with open_checkpointer(settings.checkpointer, settings.db.url) as checkpointer:
                graph = _default_compiled_graph(settings, checkpointer=checkpointer)
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

    app.include_router(health_router)
    app.include_router(make_investigations_router(principal_dep=make_principal_dep(settings)))

    return app


# For uvicorn, use the factory pattern so we don't crash at import time when
# Postgres/LLM creds aren't configured:
#
#   uvicorn --factory kubepilot_api.main:build_app
#
# Tests call ``build_app(repo=..., compiled_graph=...)`` directly with in-memory deps.
