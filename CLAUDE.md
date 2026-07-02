# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KubePilot AI — an open-source **read-only** Agentic SRE platform for Kubernetes. A LangGraph
multi-agent system investigates production incidents by correlating signals across the k8s API,
Prometheus, Loki, Tempo, and CI/CD, and produces a root-cause report with evidence + confidence.
Python 3.12, `uv` workspace of services + a Next.js Web UI, shipped as one Helm chart.

**Phase discipline (binding):** read-only through Phase 3; **Phase 4 adds writes, but they are OFF by
default and HITL-gated.** The write path exists only when `remediation.enabled=true`: a separate
`mcp-k8s-write` server (curated tools, dry-run unless `applyEnabled=true`) with its own least-privilege
ClusterRole, reached only through policy → blast-radius → human-approval → executor (kill-switch +
audit) → auto-rollback. Never add a write outside that gate; never widen the write ClusterRole beyond
`mcp_k8s_write.safety.required_rbac()` (a rendered-chart test enforces it). See
`docs/reference/roadmap.md` and the phase plans.

## Commands

```bash
make install                 # uv sync --all-packages (creates .venv, fetches py3.12)
make test                    # unit tests (excludes integration + live_llm)
make lint                    # ruff check .
make format                  # ruff format .
make typecheck               # mypy (non-blocking in CI — see note below)
make check                   # lint + typecheck + tests

# One test / file (pass an explicit path — it overrides testpaths):
uv run pytest services/orchestrator/tests/test_state.py::test_v2_fixture_loads_under_current_schema -p no:cacheprovider

# Eval harness (golden RCA scenarios)
make eval-test               # deterministic self-test, no LLM key (this is what CI runs)
make eval                    # full golden run against a live LLM (needs ANTHROPIC_API_KEY or OPENAI_API_KEY)
uv run pytest eval -p no:cacheprovider   # eval/ is NOT in testpaths, so run it explicitly

# Local dev + demo
make dev-up                  # docker-compose Postgres(pgvector) + Redis
make smoke-test              # validates LLM provider + DB connectivity
make minikube-up             # full end-to-end demo on minikube (export OPENAI_API_KEY first)

# Web UI
cd services/web-ui && npm run build

# Chart
helm lint charts/kubepilot-ai
helm template kp charts/kubepilot-ai -f charts/kubepilot-ai/values-local.yaml -n kubepilot-system
```

Test config: `asyncio_mode=auto`, `testpaths=["services/*/tests"]`, `--import-mode=importlib`. Markers:
`integration` (needs running pg/redis/MCP), `slow`, `live_llm` (needs a real key). CI runs
`-m "not integration and not live_llm"` plus `pytest eval`.

## Architecture (the parts that span files)

**Request flow.** Web UI / CLI / Slack → **api-gateway** (FastAPI) → **LangGraph orchestrator** → **MCP
servers** → k8s/Prom/Loki/Tempo/CI. Results persist to Postgres; SSE streams progress back.

**The api-gateway hosts the orchestrator in-process.** There is no standalone orchestrator server — the
gateway imports `kubepilot_orch` and runs the compiled graph itself (`api-gateway/.../main.py`
`_default_compiled_graph` + `_build_memory`). So the api-gateway Docker image and Helm deployment ARE
the app tier; `services/orchestrator` is a library.

**The graph is conditional** (`orchestrator/.../graph.py`). `AgentDeps` carries optional `mcp_tempo`,
`mcp_ci`, `memory`, `knowledge`, `calibrator`, and `enable_critic`. `build_graph` adds the
Tracing/Deployment specialist branches and the pre-RCA collector nodes **only when those deps are
present**, so a minimal install is the Phase-1 three-specialist shape and a full install fans out to
five specialists → {memory, knowledge} (parallel pre-RCA collectors) → RCA → **critic** →
recommendation → finalize. Parallel branches merge via reducer-annotated `InvestigationState` fields
(`operator.add` / `_merge_dicts` — including Phase 3 `prompt_versions`); serial nodes own singleton
fields.

**Phase 3 additions (all read-only).** A **critic agent** reviews the RCA before recommendation
(agreement/concerns/escalation → seeds `calibrated_confidence`; gated by `enable_critic`, on in the
gateway). A **cluster knowledge graph** (`knowledge/`, relational + optional pgvector) injects
owner/deps/SLO context via a pre-RCA node. **Runtime-specific RCA libraries** (`rca/runtimes/*.md`,
selected by the Logs agent's `detail.runtime`) are injected into the RCA prompt — data, not branching
code. A **confidence calibrator** (`calibration/`, isotonic/PAV, no sklearn) maps raw→empirical
confidence at finalize. **Guardrails** (`guardrails/`): `sanitize` scrubs prompt-injection from tool
results in `agents/_runner.py`; `policy` drops destructive recommendations. A **versioned prompt
registry** (`agents/prompt_registry.py`) powers A/B + rollback (`prompt_active_versions` config).
**RBAC v2** (`api-gateway/.../auth.py`): viewer/investigator/operator/admin + namespace scoping +
audit export (`audit.py`). **mcp-datadog** is a reference observability adapter mapping Datadog →
curated capability shapes. Eval harness gains calibration/drift/prompt-A/B/debate + a release gate
(`eval/harness/{calibration,drift,eval_gate,prompt_ab,debate_eval}.py`, `.github/workflows/eval-gate.yml`).

**Phase 4 additions (gated WRITES, off by default).** State v4 adds `remediation_plan`/`approvals`/
`executions`/`rollbacks`/`remediation_outcome` (additive; v1–v4 fixture-replay). When
`enable_remediation`, the graph runs `recommendation → remediation → [interrupt_before execute] →
execute_remediation → finalize`; the interrupt IS the HITL gate (LangGraph `interrupt_before` +
checkpointer resume). The `remediation/` package is the gated path: `catalog` (write surface),
`remediation_agent` (executable plan, catalog-filtered), `policy` (default-deny YAML engine +
`policies/*.yaml`), `blast_radius`, `approval` (approver RBAC + status/expiry), `executor` (kill-switch
→ policy → blast cap → `mcp-k8s-write` invoke → audit; process-global `set_kill_switch`), `rollback`
(inverse actions on regression), `validation` (re-check → close/reopen), `selfheal` (opt-in per
pattern, still fully gated). Approve/reject + kill-switch live in `api-gateway/.../routes/approvals.py`.
`mcp-k8s-write` is a *separate* server (curated tools, dry-run unless `KUBEPILOT_WRITE_APPLY_ENABLED`)
with a Helm least-privilege ClusterRole matching `safety.required_rbac()` + a NetworkPolicy, all gated
on `remediation.enabled`; the `remediation-e2e.yml` kind sandbox is the only place real writes run.

**MCP servers speak a REST contract, not stdio MCP.** Every server (`mcp-k8s/prom/loki/tempo/ci`)
exposes `GET /mcp/tools`, `POST /mcp/invoke`, `GET /mcp/health` and returns **curated** response models
(e.g. `PodSummary` derives `status_reason`; `TraceSummary` picks the slowest span) — not raw API
objects. They mirror `mcp-prom`'s structure (a `_Registry` dispatch table, tenacity-retried httpx
client, per-tool JSON schema with `additionalProperties:false`). The orchestrator's `MCPClient` +
`CapabilityRouter` (`orchestrator/.../mcp/adapter.py`) map a capability domain → a client, so pointing
several domains at one URL (e.g. a Grafana MCP) is a config-only swap.

**LLM provider abstraction** (`orchestrator/.../llm/`). One `LLMProvider.chat()` behind a router that
picks provider+model per **role** (`routing`/`analysis`/`summarization`) from config. Six providers:
Anthropic, OpenAI, Bedrock, Azure, Ollama, vLLM (vLLM subclasses OpenAI). Critical contract, learned
the hard way (see `base.py`):
- Providers **do not validate structured output and must not raise on bad JSON** — the *caller*
  validates and owns the fallback (`agents/_runner.py`, `rca_agent.py`, `recommendation_agent.py`).
- Callers must `strip_code_fences()` (`llm/parsing.py`) before `model_validate_json` — models like
  gpt-4o-mini wrap JSON in ```` ```json ```` fences.
- An assistant `Message` that requested tools **must carry `tool_calls`** so the following `tool`
  message is valid (OpenAI rejects it otherwise). All provider `_to_lc` conversions render this.
- Fields filled by code after validation (`AgentOutput.agent_name/succeeded`,
  `Evidence.source_agent/collected_at`) **default** in `state.py` — requiring them rejects real model
  output. Keep code-filled fields optional.

**Long-term memory** (`orchestrator/.../memory/`). On finalize, the concluded incident is embedded and
stored; before RCA, a memory node retrieves similar past incidents into `state.memory_context` (hybrid:
dense similarity + metadata boost). Two embedders (offline `HashEmbedder`, BYOK `OpenAIEmbedder`) and
two stores (`InMemoryMemoryStore` for dev/tests, `PgVectorMemoryStore` for prod). Memory is
corroborating context for RCA, never overrides current signals.

**State schema versioning is a hard discipline** (`orchestrator/.../state.py`, ARCHITECTURE §3.2.1).
`InvestigationState` is serialized to Postgres at every node. Rules enforced by `tests/test_state.py`:
additive-only between bumps; **every** bump needs a `MIGRATIONS[n]` entry (trivial version-stamp for
additive, real transform for breaking) and `CURRENT_SCHEMA_VERSION` incremented; add a
`tests/fixtures/checkpoints/vN_sample.json` and the fixture-replay test must load **all** historical
versions. `test_migration_registry_is_complete` fails if you bump the version without a migration.

**Read-only guarantee is enforced at two layers.** `mcp-k8s` exposes only read tools (no
`get_secret`; `get_configmap` returns keys only), and the Helm ClusterRole grants only
`get/list/watch`. `mcp-k8s/tests/test_rbac.py` renders the chart and asserts no write verbs — it
**skips silently if `helm` isn't installed**, so CI installs helm to make it run.

## Conventions & gotchas

- **Every service must declare all its runtime deps in its own `pyproject.toml`.** The dev venv
  resolves many deps transitively; the Docker image runs `uv sync --frozen --package <pkg>` and only
  gets *declared* deps. Undeclared-but-used imports (this bit `tenacity` in the MCP servers,
  `sse-starlette` in the gateway) pass tests but `ModuleNotFoundError` in-cluster.
- **`ScriptedLLM` (test helper) bypasses provider message conversion.** So provider-level bugs
  (tool_calls format, code fences, message shaping) are invisible to scripted unit tests — they only
  surface against a real LLM. Validate provider/agent-loop changes with `make minikube-up` or a
  `live_llm` test, not just scripted tests.
- **mypy is non-blocking in CI.** Strict mypy has pre-existing errors (missing third-party stubs) and
  duplicate test-module names across services; `make typecheck` runs it but CI has `continue-on-error`.
  Don't assume mypy-clean.
- **Style:** `from __future__ import annotations` everywhere, ruff line-length 100, tz-aware UTC
  datetimes only (`datetime.now(UTC)` / `fromtimestamp(x, tz=UTC)`), structlog. Prompts live in
  `orchestrator/.../prompts/*.md`, loaded at runtime — not inline.
- **Helm:** one umbrella chart. Resource names are `kubepilot-ai-<component>` (the fullname helper
  collapses release==chart). Profiles: `values-dev/-prod-small/-prod-air-gapped/-local/-grafana-mcp`.
  Deployed Python containers use `readOnlyRootFilesystem: true`, so anything needing a writable temp dir
  (e.g. tiktoken via langchain_openai) needs an `emptyDir` at `/tmp` (the api-gateway has one).
- **Web UI:** Next.js inlines `NEXT_PUBLIC_*` at **build** time — a runtime env var never reaches the
  browser bundle. The API needs CORS for the browser SPA (`KUBEPILOT_API_CORS_ORIGINS`). Don't render
  raw objects as React children (evidence `detail` is an object).
- **Git/workflow:** work happens on `phase*/…` feature branches merged via PR into `main`; commit
  messages end with the `Co-Authored-By` trailer.

## Where to read more

`docs/README.md` is the index. `docs/reference/architecture.md` is the engineering source of truth;
`phase-1-plan.md` / `phase-2-plan.md` hold the Definition-of-Done checklists;
`docs/getting-started/minikube.md` is the one-command local demo.
