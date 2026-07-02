# KubePilot AI

> **Open-source Agentic SRE Platform for Kubernetes.**
> Your AI SRE teammate for production incident investigation across any workload.

KubePilot AI investigates Kubernetes incidents autonomously by correlating signals across the cluster API, Prometheus, Loki, Tempo, and your CI/CD pipelines. It runs **any containerized workload** — Java, Python, Node.js, Go, .NET, databases, queues — and produces a root-cause analysis with evidence and confidence scoring, with **zero write access** to your cluster.

**Status:** Phase 1 (MVP) — **feature-complete, pre-release.** All six agents, the three MCP servers, all six LLM providers, the Postgres-checkpointed LangGraph, the Web UI, the deployable Helm chart (dev / prod-small / prod-air-gapped), AgentOps token accounting, and a 20-scenario eval harness are implemented and tested. Remaining before `v0.1.0`: a tagged release, demo video, and external-tester validation. See [Phase 1 Plan](docs/reference/phase-1-plan.md).

## Why KubePilot

| Pain | KubePilot's answer |
|---|---|
| Engineers spend hours correlating signals across k8s, Prom, Loki, Grafana, Jenkins | A multi-agent system does it in seconds, in parallel |
| Knowledge lives in senior SREs' heads | Codified in agent prompts + cluster knowledge graph (Phase 3) |
| "AI SRE" tools assume cloud SaaS access | Self-hosted, air-gappable (Ollama + vLLM), BYOK for cloud LLMs |
| Most AI tools target ML workloads | We target **any** k8s workload — language- and runtime-agnostic |

## Architecture

```text
Web UI / CLI / Slack
        ↓
   API Gateway (FastAPI)
        ↓
LangGraph Supervisor ──┬─ Kubernetes Agent ──→ k8s-mcp
                       ├─ Metrics Agent    ──→ prom-mcp
                       ├─ Logs Agent       ──→ loki-mcp
                       ├─ Tracing Agent    ──→ tempo-mcp (P2)
                       └─ Deployment Agent ──→ ci-mcp    (P2)
                                    ↓
                              RCA Agent ──→ Recommendation Agent
                                    ↓
                            Postgres + pgvector
                            Redis (cache)
```

Full engineering view in [Architecture](docs/reference/architecture.md). Product vision in [IDEA.md](IDEA.md). Multi-phase roadmap in [Roadmap](docs/reference/roadmap.md).

📚 **All documentation:** [docs/README.md](docs/README.md) — start there.

## Try it locally (minikube, ~1 command)

```bash
export OPENAI_API_KEY=sk-...     # BYOK; loaded into a Secret, never written to a file
make minikube-up                 # builds images, installs Prom+Loki + KubePilot, deploys demo workloads
```

Full walkthrough: [Run on minikube](docs/getting-started/minikube.md).

## Quick Start (Local Dev)

**Prerequisites:** Python 3.12 (auto-installed by `uv`), Docker, [uv](https://docs.astral.sh/uv/), and [kind](https://kind.sigs.k8s.io/) (for end-to-end tests).

```bash
# Install dependencies (creates .venv, fetches Python 3.12 if missing)
make install

# Start Postgres + Redis locally
make dev-up

# Verify everything wired up (LLM provider + DB connectivity)
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, or run an Ollama server
make smoke-test

# Run tests
make test
```

Once Phase 1 ships (`v0.1.0`), the standard install will be `helm install kubepilot-ai`.

## Locked Product Decisions

These five decisions are binding for the initial release. See [IDEA.md](IDEA.md) for full rationale.

| Decision | Choice |
|---|---|
| MVP action posture | **Read-only investigator** — no cluster writes until Phase 4 |
| Distribution | **Self-hosted OSS via Helm** — no SaaS in initial scope |
| Observability stack (P1) | **Grafana LGTM only** (Prometheus + Loki + Tempo) |
| LLM strategy | **BYOK multi-provider + local models** (Anthropic, OpenAI, Bedrock, Azure, Ollama, vLLM) |
| Workload coverage | **Any containerized workload** (not specific to AI/ML) |

## Roadmap

| Phase | Theme | Status |
|---|---|---|
| 1 | MVP — read-only RCA across k8s/Prom/Loki | 🚧 In progress |
| 2 | Production-ready: MCP, memory, timeline, Slack, CLI | 🚧 In active implementation |
| 3 | Enterprise: multi-agent, eval framework, knowledge graph | ⏳ Planned |
| 4 | Autonomous: HITL-gated remediation, auto-rollback, self-healing | ⏳ Planned |

> **Phase 2 is landing now (not yet released).** The Tracing + Deployment
> specialists (`mcp-tempo` / `mcp-ci`), long-term pgvector memory, the Slack bot,
> and the `kubepilot` CLI are implemented behind default-off Helm flags. See the
> Phase 2 docs: [tracing & CI](docs/features/tracing-and-ci.md) ·
> [memory](docs/features/memory.md) · [Slack](docs/features/slack.md) · [CLI](docs/features/cli.md) ·
> [MCP adapters](docs/configuration/mcp-adapters.md). Full plan in
> [Phase 2 Plan](docs/reference/phase-2-plan.md).

Full phase-by-phase detail in [Roadmap](docs/reference/roadmap.md).

## License

[Apache 2.0](LICENSE)

## Author

**Akash Kumar Sahani** — Agentic AI Engineer · AI Infrastructure Engineer · Kubernetes & AgentOps
