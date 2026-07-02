# Changelog

All notable changes to KubePilot AI are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). The platform is **read-only through
Phase 3** — no code path writes to the cluster (writes are Phase 4).

## [Unreleased]

### Added — gap fixes (post-Phase-3 wiring)
- **Knowledge-graph ingestion**: `python -m kubepilot_orch.knowledge.ingest_cli`
  + a startup snapshot ingest + a Helm `knowledge-ingest` CronJob — the graph is
  now actually populated in production (previously an empty store).
- **Web UI** surfaces Phase 3 signals: escalate-to-human banner, calibrated
  confidence, critic review (agreement + concerns), and cluster-knowledge cards.
- **Datadog adapter deployable**: `mcp-datadog` Deployment/Service templates +
  `values-datadog.yaml` profile (config-only metrics/logs swap).
- **Phase 3 config plumbed via Helm**: `apiGateway.phase3` (critic / knowledge /
  calibrator / prompt pins) rendered into the gateway Deployment.
- **`GET /calibration`** exposes the confidence-calibration map for the plot.
- **Held-out RCA eval set** (`heldout_rca_scenarios.jsonl`) scored separately from
  golden to detect overfitting.
- **Integration tests** (real Postgres + pgvector) for the memory and knowledge
  stores, gated behind a new CI job with a pgvector service container.
- Nightly eval now also runs memory-A/B + timeline + held-out.
- `scripts/inject-failures.sh` (demo failure injector) and a `helm-publish.yml`
  workflow (OCI chart publish on tags).

### Fixed — surfaced by an end-to-end minikube run
- **Graph deadlock with memory + knowledge both enabled**: the two pre-RCA nodes
  ran in parallel and both wrote the singleton `current_step`, so LangGraph raised
  `InvalidUpdateError` and every investigation failed. They now run as a serial
  chain (memory → knowledge → rca).
- **Missing `critique` LLM role**: the Helm `llm.roles` config omitted the Phase 3
  critique role, crashing the critic. The router now falls back to the analysis
  binding for any unconfigured role, and `critique` is added to all values
  profiles.

## [0.3.0] — Phase 3: enterprise-grade (not yet tagged)

### Added
- **Multi-agent critique**: a critic agent reviews the RCA (agreement, concerns,
  critic-adjusted confidence, escalate-to-human) between RCA and recommendation.
- **Cluster knowledge graph**: services ↔ owners ↔ dependencies ↔ SLOs, injected
  into the RCA as corroborating context.
- **Runtime-specific RCA libraries** (JVM/Node/Python/Go) selected by the Logs
  agent's `detail.runtime` — data, not branching code.
- **Confidence calibration** (isotonic/PAV, no sklearn) + Expected Calibration
  Error + reliability curve; `calibrated_confidence` stamped at finalize.
- **Continuous eval + drift detection + release gate** blocking >5% regression.
- **Prompt versioning + A/B + rollback** (config-pin), recorded per investigation.
- **Guardrails**: prompt-injection sanitization of tool results + a
  forbidden-recommendation policy.
- **RBAC v2**: viewer/investigator/operator/admin + namespace scoping + SIEM
  audit export.
- **Observability adapter interface** + a **Datadog** reference MCP server.
- State schema **v3** (additive) with v1/v2/v3 fixture-replay.

## [0.2.0] — Phase 2: production-ready (not yet tagged)

### Added
- Long-term **incident memory** (pgvector): retrieve-before-RCA, embed-on-finalize.
- **Incident timeline** construction.
- **Tracing** (mcp-tempo) + **Deployment/CI** (mcp-ci) specialists.
- MCP **capability router** — config-only backend swaps (e.g. a Grafana MCP).
- Light **multi-tenancy** (viewer/investigator roles + namespace-scoped keys).
- **Slack bot** + **CLI**; Postgres **checkpointer** (resumable investigations).

## [0.1.0] — Phase 1: MVP (not yet tagged)

### Added
- LangGraph multi-agent investigation: supervisor → Kubernetes/Metrics/Logs
  specialists → RCA → recommendation → finalize.
- Read-only **MCP servers** (mcp-k8s/prom/loki) returning curated response models;
  read-only RBAC (get/list/watch) enforced in code + chart.
- **LLM provider abstraction** (Anthropic/OpenAI/Bedrock/Azure/Ollama/vLLM) with
  per-role routing.
- Golden **RCA eval harness**; AgentOps (OTel + token ledger); **Web UI**; one
  umbrella **Helm chart** with dev / prod-small / prod-air-gapped profiles.

[Unreleased]: https://github.com/akashracloop/kubepilot/commits/main
