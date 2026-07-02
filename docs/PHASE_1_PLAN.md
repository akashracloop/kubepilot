# KubePilot AI — Phase 1 Implementation Plan

> **Goal:** Ship a working, read-only, autonomous incident investigator for any workload on a single Kubernetes cluster, distributed as a Helm chart. Demonstrable end-to-end in 12 weeks of focused work.

> Reference: [ARCHITECTURE.md](./ARCHITECTURE.md) (the *what*), this doc (the *how*), [ROADMAP.md](./ROADMAP.md) (the *when across phases*).

---

## 1. Success Criteria

Phase 1 is **done** when all of these are true:

1. A user can `helm install kubepilot-ai` into a Kubernetes cluster and reach a working Web UI in under 10 minutes.
2. The user can run an investigation: *"Why is `<service>` failing?"* and receive an RCA report with root cause, confidence score, evidence list, and recommended actions — **without any human intervention during the investigation**.
3. The investigation pulls live signals from K8s API, Prometheus, and Loki via MCP servers.
4. The system makes zero writes to the cluster (verified by the bound ClusterRole containing only read verbs).
5. LLM provider is BYOK and configurable: works with Anthropic, OpenAI, Bedrock, Azure, Ollama, vLLM.
6. AgentOps observability (LangSmith or Phoenix) shows traces, token usage, and tool calls for every investigation.
7. A golden eval suite of ≥20 RCA scenarios runs on every commit; baseline accuracy ≥70%.
8. README + architecture docs + Helm install guide are published.

---

## 2. Scope

### 2.1 In Scope

| Item | Detail |
|---|---|
| Agents | Supervisor, Kubernetes, Metrics, Logs, RCA, Recommendation |
| MCP servers | `k8s-mcp`, `prom-mcp`, `loki-mcp` |
| LLM providers | Anthropic, OpenAI, Bedrock, Azure OpenAI, Ollama, vLLM |
| UI | Web Dashboard (Next.js): trigger investigation, view RCA report, view live trace |
| Persistence | Postgres (incidents + LangGraph checkpoints), Redis (cache) |
| Auth | Single-user mode (API token) for OSS; OIDC/Keycloak optional |
| AgentOps | Phoenix (default, self-hosted), LangSmith (BYO key, optional) |
| Eval | Golden dataset + LangSmith integration; pytest harness |
| Packaging | Helm chart with `dev`, `prod-small`, `prod-air-gapped` profiles |
| Docs | README, Architecture, Install guide, Configuring LLM providers, Troubleshooting |

### 2.2 Out of Scope (Explicitly)

- Any cluster writes (no `kubectl apply`, no rollbacks, no remediation execution)
- Tracing agent + Tempo MCP (Phase 2)
- Deployment agent + CI MCP (Phase 2)
- Long-term memory / pgvector RAG over past incidents (Phase 2)
- Slack bot, CLI (Phase 2)
- Multi-cluster
- Datadog, New Relic, ELK, Splunk integrations
- SaaS control plane
- Multi-tenancy (beyond single-user OSS install)
- Fine-tuning

---

## 3. Repository Structure

Monorepo, since components ship together via one Helm chart.

```text
kubepilot-ai/
├── README.md
├── IDEA.md
├── LICENSE                              (Apache 2.0)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PHASE_1_PLAN.md
│   ├── ROADMAP.md
│   ├── install.md
│   ├── llm-providers.md
│   └── troubleshooting.md
├── services/
│   ├── api-gateway/                     (FastAPI, REST endpoints, auth)
│   │   ├── pyproject.toml
│   │   ├── src/kubepilot_api/
│   │   └── tests/
│   ├── orchestrator/                    (LangGraph runtime, agents)
│   │   ├── pyproject.toml
│   │   ├── src/kubepilot_orch/
│   │   │   ├── agents/
│   │   │   │   ├── supervisor.py
│   │   │   │   ├── kubernetes_agent.py
│   │   │   │   ├── metrics_agent.py
│   │   │   │   ├── logs_agent.py
│   │   │   │   ├── rca_agent.py
│   │   │   │   └── recommendation_agent.py
│   │   │   ├── prompts/                 (version-controlled, .md files)
│   │   │   ├── llm/                     (provider abstraction)
│   │   │   ├── graph.py                 (LangGraph wiring)
│   │   │   └── state.py
│   │   └── tests/
│   ├── mcp-k8s/                         (Kubernetes MCP server)
│   ├── mcp-prom/                        (Prometheus MCP server)
│   ├── mcp-loki/                        (Loki MCP server)
│   └── web-ui/                          (Next.js)
├── charts/
│   └── kubepilot-ai/                    (umbrella Helm chart)
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── values-dev.yaml
│       ├── values-prod-small.yaml
│       ├── values-prod-air-gapped.yaml
│       ├── templates/
│       └── charts/                      (subchart deps: postgres, redis, phoenix)
├── eval/
│   ├── datasets/
│   │   └── golden_rca_scenarios.jsonl   (20+ labeled scenarios)
│   ├── harness/                         (pytest + LangSmith)
│   └── README.md
├── scripts/
│   ├── dev-cluster.sh                   (kind cluster + sample workloads)
│   └── inject-failures.sh               (CrashLoop / OOM / config-error scenarios)
├── .github/workflows/
│   ├── ci.yml                           (lint, test, build)
│   ├── helm-publish.yml
│   └── eval.yml                         (nightly golden eval)
└── pyproject.toml                       (workspace config, ruff, mypy)
```

---

## 4. Milestones

Estimated for one full-time engineer. Compress if working with collaborators.

| Week | Milestone | Deliverable | Verification |
|---|---|---|---|
| **W1** | Foundations | Repo scaffolded, CI green, dev kind cluster scripts, LLM provider abstraction working (single Claude call end-to-end), Postgres + Redis running in dev | `make dev-up` → `make smoke-test` passes |
| **W2** | k8s-mcp | First MCP server running. Tools: `list_pods`, `describe_pod`, `get_events`. RBAC ClusterRole defined. Helm subchart. | Invoke via MCP inspector; verify read-only RBAC |
| **W3** | prom-mcp + loki-mcp | Both MCP servers exposing query tools. Wired to a dev Prom/Loki stack. | Run a `query_metrics` and `query_logs` from CLI client |
| **W4** | Kubernetes Agent | LangGraph node that uses k8s-mcp to assess service health. Returns structured output. | Unit test: agent on a CrashLoopBackOff fixture returns correct pod state summary |
| **W5** | Metrics Agent + Logs Agent | Both agents implemented; parallel execution wired in LangGraph | Test investigates a fake OOM, both agents produce evidence |
| **W6** | Supervisor + RCA Agent | Supervisor routing, RCA correlation, structured output schema | End-to-end LangGraph run on a fixture incident produces RCA report |
| **W7** | Recommendation Agent + API Gateway | FastAPI exposes `POST /investigations`, `GET /investigations/{id}`. Streams progress via SSE. | `curl` triggers investigation, streams result |
| **W8** | Web UI | Next.js: trigger form, live trace view, RCA report rendering | Manual browser walk-through against dev cluster |
| **W9** | AgentOps + Phoenix | Phoenix deployed in chart. OTel-GenAI traces flowing. Token cost ledger in Postgres. | Open Phoenix UI, see traces for a test investigation |
| **W10** | Eval harness | 20 golden scenarios written. pytest + LangSmith integration. Baseline accuracy measured. | `make eval` produces score report; CI runs it on PR |
| **W11** | Helm packaging + profiles | Single `helm install` works for `dev`, `prod-small`, `prod-air-gapped`. Ollama subchart wired. | Fresh kind cluster → helm install → working investigation |
| **W12** | Docs + Demo + Release | README, install guide, LLM provider guide, troubleshooting. v0.1.0 GitHub release + demo video. | External tester (not you) installs and runs an investigation following docs alone |

**Buffer week (W13)** — bugs found during W11–W12 validation. Always there. Don't skip.

---

## 5. Component Deliverables (Detail)

### 5.1 `orchestrator` — LangGraph Runtime

**Definition of done:**
- LangGraph compiled graph wiring Supervisor → (K8s ∥ Metrics ∥ Logs) → RCA → Recommendation.
- Postgres-backed checkpointer (`langgraph.checkpoint.postgres`).
- All agents return Pydantic-validated structured outputs.
- Prompts live in `prompts/*.md`, loaded at startup, hot-reloadable in dev.
- LLM provider abstraction passes a contract test against all 6 providers (mock for cloud, real for local).

**Acceptance test:**
```python
def test_investigation_oom_scenario(fixture_oom_cluster):
    result = orchestrator.investigate(
        query="why is payment-service failing?",
        namespace="prod"
    )
    assert result.root_cause_category in {"OOMKilled", "MemoryPressure"}
    assert result.confidence >= 0.7
    assert len(result.evidence) >= 3
```

### 5.2 `mcp-k8s` — Kubernetes MCP Server

**Tools (read-only):**
- `list_pods(namespace, label_selector?)`
- `describe_pod(namespace, name)` — returns spec + status + recent events
- `get_events(namespace, related_to?)`
- `get_nodes()`
- `get_deployments(namespace)`
- `get_services(namespace)`
- `get_pvcs(namespace)`
- `get_configmap(namespace, name)`

**RBAC:** Helm chart binds ServiceAccount to a ClusterRole containing only `get`, `list`, `watch` verbs on core/apps API groups. **Test:** attempt a `create` from inside the pod — must be denied by k8s itself.

### 5.3 `mcp-prom` — Prometheus MCP Server

**Tools:**
- `query_metrics(promql)` — instant query
- `query_range(promql, start, end, step)` — range query
- `list_targets()` — for discovery
- `query_alerts()` — current firing alerts

**Connectivity:** Configurable Prometheus URL + Bearer token via k8s Secret.

### 5.4 `mcp-loki` — Loki MCP Server

**Tools:**
- `query_logs(logql, start, end, limit)`
- `search_errors(service, time_range)` — convenience wrapper
- `search_exceptions(service, time_range)` — matches stack-trace patterns across runtimes (Java, Python, Node, Go)

**Workload-agnostic guarantee:** `search_exceptions` uses regex patterns covering JVM (`Exception`, `Error:`, `Caused by:`), Python (`Traceback`), Node (`UnhandledPromiseRejection`, `TypeError:`), Go (`panic:`, `goroutine N \[`), and generic (`FATAL`, `PANIC`). Verified in unit tests against fixture logs from each runtime.

### 5.5 `api-gateway`

**Endpoints (Phase 1):**
```text
POST   /investigations              { query, namespace, service? } → { investigation_id }
GET    /investigations              list with pagination
GET    /investigations/{id}         full record
GET    /investigations/{id}/stream  SSE stream of agent progress
GET    /health                      liveness
GET    /ready                       readiness (checks MCP servers, DB, LLM provider)
```

**Auth (Phase 1):** Static API token from k8s Secret. OIDC stub in place but optional.

### 5.6 `web-ui`

**Pages:**
- `/` — Trigger investigation form (service, namespace, time window)
- `/investigations` — List of past investigations
- `/investigations/[id]` — Live view: streaming agent progress, evidence accumulating, final RCA report card with confidence + recommendations

**Component library:** ShadCN/UI. No design system bikeshedding — use defaults.

---

## 6. LLM Provider Abstraction

**Contract:**
```python
class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        response_schema: type[BaseModel] | None = None,
        role: Literal["routing", "analysis", "summarization"] = "analysis",
    ) -> LLMResponse: ...
```

**Implementations (all Phase 1):**
- `AnthropicProvider` — claude-sonnet-4-6 / claude-opus-4-7 (config-driven)
- `OpenAIProvider` — gpt-4o / gpt-4o-mini
- `BedrockProvider` — Claude or Llama via AWS Bedrock
- `AzureOpenAIProvider` — Azure-hosted gpt-4o
- `OllamaProvider` — local; default `llama3.1:8b` or `qwen2.5:14b`
- `VLLMProvider` — OpenAI-compatible local endpoint

**Per-role model routing** configured in `values.yaml`:
```yaml
llm:
  default_provider: anthropic
  roles:
    routing:       { provider: anthropic, model: claude-haiku-4-5-20251001 }
    analysis:      { provider: anthropic, model: claude-sonnet-4-6 }
    summarization: { provider: anthropic, model: claude-haiku-4-5-20251001 }
```

**Air-gapped values.yaml:**
```yaml
llm:
  default_provider: ollama
  roles:
    routing:       { provider: ollama, model: llama3.1:8b }
    analysis:      { provider: vllm,   model: qwen2.5:14b }
    summarization: { provider: ollama, model: llama3.1:8b }
```

---

## 7. Eval Strategy

### 7.1 Golden Dataset

Hand-author **20+ scenarios** covering common k8s failure modes across runtimes. Format `eval/datasets/golden_rca_scenarios.jsonl`:

```json
{
  "id": "java-spring-oom-001",
  "fixture_cluster": "fixtures/java-oom.yaml",
  "query": "why is payment-service failing?",
  "expected": {
    "root_cause_category": "OOMKilled",
    "min_confidence": 0.7,
    "must_mention_evidence": ["memory", "restart", "exit code 137"]
  }
}
```

Scenario coverage:
- CrashLoopBackOff (Java OOM, Python segfault, Node uncaught exception, Go panic)
- ImagePullBackOff (auth, typo, registry down)
- ConfigMap/Secret typo or missing
- Pending pod (insufficient resources, NodeSelector, taint)
- Service misrouting (selector mismatch)
- Recent deployment introducing 5xx spike
- DNS resolution failure
- Disk pressure / PVC full
- Network policy blocking traffic
- ReadinessProbe failure

### 7.2 Harness

- pytest-based, runs against fixtures *or* a real kind cluster (toggleable).
- LangSmith dataset uploaded; each PR's eval run linked from CI.
- Score = `(correctly_identified_root_cause + confidence_within_tolerance + required_evidence_present) / 3`.
- Baseline target: **≥70%** at v0.1.0. Improvement to ≥85% is a Phase 3 goal.

### 7.3 CI Integration

- **PR builds:** Run a fast subset (5 scenarios, mocked LLM with cassettes via `vcrpy`).
- **Nightly:** Full 20+ scenarios against a real LLM, results posted to LangSmith.
- **Release gate:** Cannot tag a release if eval drops >5% from prior release.

---

## 8. Testing Strategy

| Layer | Type | Tool |
|---|---|---|
| Unit | Pure function tests for agents (LLM mocked) | pytest |
| Contract | LLM provider conformance | pytest + cassette fixtures |
| Integration | Agent + MCP server (real Postgres, real MCP, mocked external APIs) | pytest + testcontainers |
| End-to-end | Full Helm install in kind, real Prom + Loki, fixture workloads | bash + kind + helm |
| Eval | Golden RCA scenarios | pytest + LangSmith |
| UI | Component + interaction | Playwright (smoke only in Phase 1) |

**Coverage target:** 70% line coverage on `orchestrator` and MCP servers. Less on UI (manual testing acceptable in Phase 1).

---

## 9. Demo Acceptance Criteria

The v0.1.0 demo video must show:

1. `helm install kubepilot-ai` on a fresh kind cluster (with sample workloads pre-deployed).
2. Open Web UI, log in.
3. Inject a failure: `./scripts/inject-failures.sh oom payment-service`.
4. Trigger investigation: *"Why is payment-service failing?"*
5. Watch agents stream: K8s → Metrics → Logs → RCA → Recommendation.
6. Show final report: root cause = OOMKilled, confidence ≥ 0.85, evidence with metric chart + log lines, recommendations include rollback + memory limit increase.
7. Open Phoenix UI: show LLM traces, token cost, tool calls.
8. Show RBAC ClusterRole: no write verbs.

If any step needs manual intervention or doesn't work first try, Phase 1 isn't done.

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LangGraph state shape changes break checkpoints mid-development | Medium | Med | Pin LangGraph version; schema migration tests |
| MCP protocol still evolving | Medium | Med | Pin SDK version; track upstream weekly |
| Prom/Loki versions differ across user clusters | High | Low | Test against 3 LTS versions; document supported range |
| Air-gapped install hard to validate locally | High | Med | Maintain a kind-based "offline" test profile from W2 |
| Eval scores noisy due to LLM non-determinism | High | Med | `temperature=0`, schema enforcement, multiple-run averaging |
| Scope creep ("just add Datadog support") | High | High | Locked decisions in IDEA.md. Defer all P2+ requests to issues. |
| Building the UI eats more time than planned | Medium | Med | Stick to ShadCN defaults; no design polish in Phase 1 |
| Local LLM (Ollama) quality too weak for RCA | High | Med | Use 14B+ models (qwen2.5:14b) for analysis role; document min model size |

---

## 11. Definition of Done (v0.1.0 Release Checklist)

- [ ] All 6 agents implemented and unit-tested
- [ ] All 3 MCP servers implemented and integration-tested
- [ ] LLM abstraction passes contract tests for all 6 providers
- [ ] Helm chart installs cleanly in `dev`, `prod-small`, `prod-air-gapped` profiles
- [ ] Web UI ships and can complete a full investigation flow
- [ ] AgentOps (Phoenix) shows traces for every investigation
- [ ] 20+ golden scenarios; baseline eval score ≥70%
- [ ] CI green: lint, unit, integration, eval-subset on every PR
- [ ] README + install guide + LLM provider guide + troubleshooting guide published
- [ ] Apache 2.0 LICENSE file
- [ ] CONTRIBUTING.md + CODE_OF_CONDUCT.md
- [ ] GitHub release v0.1.0 with changelog
- [ ] Demo video (3–5 min) linked from README
- [ ] External tester (not the author) successfully runs an investigation following docs alone

---

## 12. After Phase 1

When all checkboxes above are green, move to [ROADMAP.md](./ROADMAP.md) Phase 2. **Do not start Phase 2 work before Phase 1 ships.** Scope discipline is the single biggest factor in this project succeeding.
