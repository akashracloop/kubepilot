# KubePilot AI

> **Open-source Agentic SRE Platform for Kubernetes.**
> Your AI SRE teammate for production incident investigation across any workload.

KubePilot AI investigates Kubernetes incidents autonomously by correlating signals across the cluster API, Prometheus, Loki, Tempo, and your CI/CD pipelines. It runs **any containerized workload** — Java, Python, Node.js, Go, .NET, databases, queues — and produces a root-cause analysis with evidence, an adversarial critic review, and calibrated confidence, with **zero write access** to your cluster.

**Status:** Phases 1–3 implemented and tested (read-only, pre-`v0.3.0`). The read-only investigation loop (Phase 1), production features — long-term memory, timeline, tracing/CI specialists, Slack + CLI (Phase 2) — and the enterprise layer — multi-agent critique, cluster knowledge graph, runtime-specific RCA, confidence calibration, prompt versioning/A-B, guardrails, RBAC v2, and a Datadog observability adapter (Phase 3) — are all in `main`. Remaining before a tagged `v0.3.0`: a live golden-eval accuracy run, external adopters, and the release tag. See the [Roadmap](docs/reference/roadmap.md).

## Why KubePilot

| Pain | KubePilot's answer |
|---|---|
| Engineers spend hours correlating signals across k8s, Prom, Loki, Grafana, CI | A multi-agent system does it in seconds, in parallel |
| Knowledge lives in senior SREs' heads | Codified in versioned agent prompts + a cluster knowledge graph (owners/deps/SLOs) |
| "AI SRE" tools assume cloud SaaS access | Self-hosted, air-gappable (Ollama + vLLM), BYOK for cloud LLMs |
| Most AI tools target ML workloads | We target **any** k8s workload — language- and runtime-agnostic |
| LLMs hallucinate confident wrong answers | An adversarial **critic** + **calibrated confidence** + evidence-cited RCA + guardrails |

## Architecture

```text
Web UI / CLI / Slack
        ↓
   API Gateway (FastAPI, hosts the orchestrator in-process)
        ↓
LangGraph Supervisor ──┬─ Kubernetes Agent ──→ mcp-k8s
                       ├─ Metrics Agent    ──→ mcp-prom   (or mcp-datadog)
                       ├─ Logs Agent       ──→ mcp-loki   (or mcp-datadog)
                       ├─ Tracing Agent    ──→ mcp-tempo  (P2, optional)
                       └─ Deployment Agent ──→ mcp-ci      (P2, optional)
                                    ↓
                    Memory ▸ Knowledge  (pre-RCA context: past incidents + owner/deps/SLOs)
                                    ↓
                              RCA Agent  (evidence-cited root cause + runtime library)
                                    ↓
                              Critic Agent  (adversarial review → agreement, escalate-to-human)
                                    ↓
                         Recommendation Agent → Finalize (calibrated confidence, timeline)
                                    ↓
                   Postgres + pgvector (memory + knowledge graph) · Redis (cache)
```

Every MCP server speaks a REST contract and returns **curated** response models, so the metrics/logs backends are a config-only swap (Grafana LGTM, or the Datadog adapter). Full engineering view in [Architecture](docs/reference/architecture.md). Product vision in [IDEA.md](IDEA.md). Multi-phase roadmap in [Roadmap](docs/reference/roadmap.md).

📚 **All documentation:** [docs/README.md](docs/README.md) — start there.

## Try it locally (minikube, ~1 command)

```bash
export OPENAI_API_KEY=sk-...     # BYOK; loaded into a Secret, never written to a file
make minikube-up                 # builds images, installs Prom+Loki + KubePilot, deploys demo workloads
```

Then inject a failure and investigate it:

```bash
./scripts/inject-failures.sh oom payment-service demo   # type name namespace
kubectl -n kubepilot-system port-forward svc/kubepilot-ai-web-ui 3000:3000   # → http://localhost:3000
```

Full walkthrough: [Run on minikube](docs/getting-started/minikube.md).

## Quick Start (Local Dev)

**Prerequisites:** Python 3.12 (auto-installed by `uv`), Docker, [uv](https://docs.astral.sh/uv/), and [kind](https://kind.sigs.k8s.io/) or [minikube](https://minikube.sigs.k8s.io/) for end-to-end runs.

```bash
make install       # uv sync --all-packages (creates .venv, fetches Python 3.12)
make dev-up        # local Postgres (pgvector) + Redis via docker-compose

export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, or run an Ollama server
make smoke-test    # validate LLM provider + DB connectivity

make check         # lint + typecheck + unit tests
```

Install into any cluster with the umbrella Helm chart:

```bash
helm install kubepilot-ai ./charts/kubepilot-ai -n kubepilot-system --create-namespace \
  -f charts/kubepilot-ai/values-prod-small.yaml
```

Profiles: `values-dev` · `values-prod-small` · `values-prod-air-gapped` (Ollama/vLLM, no cloud key) · `values-grafana-mcp` (one Grafana MCP for metrics/logs/traces) · `values-datadog` (Datadog adapter). See [Install](docs/getting-started/install.md).

## Feature highlights

- **Multi-agent RCA** — supervisor fans out to Kubernetes / Metrics / Logs (+ Tracing / Deployment) specialists, then an RCA agent correlates the evidence into a cited root cause.
- **Adversarial critic + calibrated confidence** — a critic agent refutes the RCA (agreement score, concerns, escalate-to-human); an isotonic calibrator maps raw confidence to empirical accuracy.
- **Cluster knowledge graph** — services ↔ owners ↔ dependencies ↔ SLOs, injected so the RCA names the owning team and weighs a dependency as a suspect.
- **Runtime-specific reasoning** — JVM/Node/Python/Go failure libraries injected by the detected runtime (data, not code).
- **Long-term memory** — pgvector hybrid retrieval of similar past incidents (retrieve-before-RCA, embed-on-finalize).
- **Guardrails** — prompt-injection sanitization of tool output + a forbidden-recommendation policy (nothing destructive is ever suggested).
- **Evaluation harness** — golden + held-out RCA scenarios, drift detection, prompt A/B, and a release gate that blocks accuracy regressions.
- **RBAC v2** — viewer/investigator/operator/admin + namespace-scoped tokens + SIEM audit export.
- **Read-only, self-hosted, BYOK** — six LLM providers with per-role routing; air-gappable with Ollama/vLLM.

Deep-dives: [RCA quality](docs/features/rca-quality.md) · [Knowledge graph](docs/features/knowledge-graph.md) · [Guardrails](docs/features/guardrails.md) · [RBAC](docs/features/rbac.md) · [Observability adapters](docs/features/observability-adapters.md) · [Memory](docs/features/memory.md) · [Tracing & CI](docs/features/tracing-and-ci.md) · [Slack](docs/features/slack.md) · [CLI](docs/features/cli.md).

## Locked Product Decisions

These decisions are binding through the read-only phases. See [IDEA.md](IDEA.md) for full rationale.

| Decision | Choice |
|---|---|
| Action posture (P1–P3) | **Read-only investigator** — no cluster writes until Phase 4 |
| Distribution | **Self-hosted OSS via Helm** — no SaaS in initial scope |
| Observability stack | **Grafana LGTM** (Prometheus + Loki + Tempo) + a pluggable adapter (Datadog reference) |
| LLM strategy | **BYOK multi-provider + local models** (Anthropic, OpenAI, Bedrock, Azure, Ollama, vLLM) |
| Workload coverage | **Any containerized workload** (not specific to AI/ML) |

## Roadmap

| Phase | Theme | Status |
|---|---|---|
| 1 | MVP — read-only RCA across k8s/Prom/Loki | ✅ Implemented |
| 2 | Production: MCP adapters, memory, timeline, tracing/CI, Slack, CLI | ✅ Implemented |
| 3 | Enterprise: critic, knowledge graph, calibration, eval gate, guardrails, RBAC v2, Datadog | ✅ Implemented (pre-`v0.3.0`) |
| 4 | Autonomous: **writes** — HITL-gated remediation, auto-rollback, self-healing | ⏳ Planned |

> **The read/write bright line:** KubePilot writes nothing to your cluster through Phase 3. RBAC grants only `get/list/watch`, `mcp-k8s` exposes only read tools, and a guardrail blocks any destructive recommendation. Writes arrive in Phase 4 behind a separate `k8s-write-mcp` and human-in-the-loop approval.

Full phase-by-phase detail in [Roadmap](docs/reference/roadmap.md); per-phase Definition-of-Done in the [phase plans](docs/reference/).

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) (dev setup, code style, the state-schema discipline, and phase scope), the [Code of Conduct](CODE_OF_CONDUCT.md), and the [Security Policy](SECURITY.md). Changes are tracked in [CHANGELOG.md](CHANGELOG.md).

## License

[Apache 2.0](LICENSE)

## Author

**Akash Kumar Sahani** — Agentic AI Engineer · AI Infrastructure Engineer · Kubernetes & AgentOps
