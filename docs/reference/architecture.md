# KubePilot AI — System Architecture

> Companion to `IDEA.md`. This document is the **engineering** view of the system: components, contracts, data flow, deployment topology, and trade-offs. Read `IDEA.md` first for product context.

---

## 1. Design Principles

These principles bound every architectural decision. They derive from the locked product decisions in `IDEA.md`.

| Principle | Implication |
|---|---|
| **Read-only by default** | No code path in Phase 1 mutates the cluster. Write capability is gated behind a separate, off-by-default subsystem introduced in Phase 4. |
| **Workload-agnostic** | No agent, prompt, or tool assumes a specific runtime (JVM/Node/Python/Go). Language-specific reasoning lives in RCA knowledge, not in code. |
| **Provider-agnostic LLM** | All LLM calls go through one abstraction. Swapping Claude → GPT-4 → Llama-3 via Ollama is a config change, not a code change. |
| **MCP-first tool layer** | Every external system (k8s, Prom, Loki, Tempo, CI) is wrapped in an MCP server. Agents call MCP tools, not raw clients. This makes tools reusable outside KubePilot. |
| **Air-gappable** | The full stack must run in a disconnected cluster (no calls to api.anthropic.com required). Local LLM via Ollama/vLLM is a first-class deployment mode. |
| **Observable agents** | Every agent run emits traces, token counts, tool calls, and cost. AgentOps is not an afterthought. |
| **Self-hosted, Helm-shipped** | One `helm install kubepilot-ai` deploys the entire stack into a user's cluster. No external control plane. |

---

## 2. System Topology

```text
┌─────────────────────────────────────────────────────────────────┐
│                         User Interfaces                         │
│  ┌──────────────┐   ┌───────────┐   ┌─────────────┐             │
│  │ Web Dashboard│   │   CLI     │   │ Slack Bot   │ (Phase 2+)  │
│  └──────┬───────┘   └─────┬─────┘   └──────┬──────┘             │
└─────────┼─────────────────┼─────────────────┼───────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API Gateway (FastAPI)                       │
│             auth · rate-limit · request routing                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                Agent Orchestration (LangGraph)                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            Supervisor Agent (router + planner)           │   │
│  └──┬─────────┬──────────┬──────────┬──────────┬────────────┘   │
│     ▼         ▼          ▼          ▼          ▼                │
│  ┌──────┐ ┌───────┐  ┌──────┐  ┌────────┐  ┌──────────┐         │
│  │ K8s  │ │Metrics│  │ Logs │  │Tracing │  │Deployment│         │
│  │Agent │ │ Agent │  │Agent │  │ Agent  │  │  Agent   │         │
│  └──┬───┘ └───┬───┘  └──┬───┘  └───┬────┘  └─────┬────┘         │
│     │         │         │          │              │             │
│     └─────────┴─────────┴──────────┴──────────────┘             │
│                          │                                      │
│                          ▼                                      │
│                  ┌──────────────┐                               │
│                  │   RCA Agent  │  (evidence correlation)       │
│                  └───────┬──────┘                               │
│                          ▼                                      │
│                ┌──────────────────┐                             │
│                │Recommendation Ag.│                             │
│                └──────────────────┘                             │
└─────────────┬──────────────────────────────────┬────────────────┘
              │ tool calls                       │ persistence
              ▼                                  ▼
┌─────────────────────────────┐  ┌─────────────────────────────┐
│      MCP Tool Layer         │  │      Persistence Layer      │
│  ┌──────────┐ ┌──────────┐  │  │  ┌───────────────────────┐  │
│  │  K8s MCP │ │ Prom MCP │  │  │  │ Postgres + pgvector   │  │
│  └──────────┘ └──────────┘  │  │  │  (memory + incidents) │  │
│  ┌──────────┐ ┌──────────┐  │  │  └───────────────────────┘  │
│  │ Loki MCP │ │ Tempo MCP│  │  │  ┌───────────────────────┐  │
│  └──────────┘ └──────────┘  │  │  │ Redis (cache + state) │  │
│  ┌──────────┐               │  │  └───────────────────────┘  │
│  │  CI  MCP │  (Phase 2)    │  │                             │
│  └──────────┘               │  │                             │
└─────────────┬───────────────┘  └─────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│              External / Cluster-Native Systems                  │
│   Kubernetes API · Prometheus · Loki · Tempo · Jenkins (P2)     │
└─────────────────────────────────────────────────────────────────┘

                                ▲
                                │ traces / metrics / logs
                                │
┌─────────────────────────────────────────────────────────────────┐
│         AgentOps Layer (observes KubePilot itself)              │
│   LangSmith · Phoenix · OpenTelemetry-GenAI · Grafana           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Layers

### 3.1 Presentation Layer

| Component | Phase | Tech | Purpose |
|---|---|---|---|
| Web Dashboard | 1 | Next.js, TypeScript, TailwindCSS, ShadCN | Triggers investigations, renders RCA reports + timelines |
| REST API | 1 | FastAPI | Single entrypoint for all clients; OpenAPI spec generated |
| CLI | 2 | Python (`typer`) | `kubepilot investigate <service>` for terminal/CI workflows |
| Slack Bot | 2 | Slack Bolt SDK | `@kubepilot why is X failing?` for incident channels |

### 3.2 Agent Orchestration Layer

Implemented as a **LangGraph state machine**.

| Element | Description |
|---|---|
| **State** | Pydantic `BaseModel` containing `schema_version`, `incident_id`, `query`, `namespace`, `evidence[]`, `agent_outputs{}`, `current_step`, `confidence`, `messages[]`, `memory_context[]` (P2+). Persisted via LangGraph checkpointer. See [§3.2.1](#321-state-schema--versioning). |
| **Nodes** | Each agent is a node. Nodes are async functions that take state, call tools/LLM, return updated state. |
| **Edges** | Supervisor node uses conditional edges to route: which sub-agent to invoke next, when to converge to RCA, when to terminate. |
| **Checkpointing** | Postgres-backed (`langgraph.checkpoint.postgres`); allows pause/resume of investigations and survives pod restarts. |
| **Concurrency** | Independent sub-agents (Metrics + Logs + Traces) run in parallel via LangGraph's parallel branching. |

#### 3.2.1 State Schema & Versioning

LangGraph serializes `State` into Postgres at every node transition. Any change to the schema — renaming a field, changing a type, restructuring nested data — risks breaking in-flight investigations, replay of past incidents, and rolling deployments. We enforce a **5-rule discipline** to manage this without building heavy migration machinery:

1. **Pydantic `BaseModel` for State, not `TypedDict`.** Real schema, runtime validation, and a clear contract. Drift is caught at deserialization, not three nodes deep into an investigation.
2. **Embed `schema_version: int` in State itself.** Every checkpoint self-describes the schema it was written under. The current version is the field's default in the live model.
3. **Additive-only between minor schema bumps.** New fields must have a default. **Never rename, never remove, never change a type.** With defaults, checkpoints written by older code deserialize cleanly under newer code — zero migration work. ~95% of evolutions are additive.
4. **Migration functions for major version bumps.** When breaking shape is unavoidable, register `migrate_vN_to_vN+1(blob) -> blob` in a `MIGRATIONS` map. The checkpoint loader chains them on load until the blob reaches the current version. Raise `CheckpointMigrationError` if a path doesn't exist.
5. **Fixture-replay CI test.** Maintain one checkpoint blob per historical schema version under `tests/fixtures/checkpoints/`. CI asserts all of them load successfully with current code. **The single most valuable test in the persistence layer.** Refuse to merge a state-shape change without updating this fixture set.

**Reference implementation pattern:**

```python
class InvestigationState(BaseModel):
    schema_version: int = 2  # current schema version
    incident_id: UUID
    query: str
    namespace: str
    evidence: list[Evidence] = []
    agent_outputs: dict[str, AgentOutput] = {}
    confidence: float | None = None
    memory_context: list[PastIncident] = []  # added in v2, additive

MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    # entries appear only for *major* breaks; additive bumps need none
}

def load_checkpoint(blob: dict) -> InvestigationState:
    version = blob.get("schema_version", 1)
    target = InvestigationState.model_fields["schema_version"].default
    while version < target:
        if version not in MIGRATIONS:
            raise CheckpointMigrationError(from_=version, to=target)
        blob = MIGRATIONS[version](blob)
        version += 1
    return InvestigationState.model_validate(blob)
```

**Deployment discipline:**

- **Rolling deploys** tolerate in-flight investigations dying gracefully. Phase 1 investigations complete in minutes; we do not engineer for hour-long workflows yet. Document this user-facing.
- **Major version bumps** must ship migration + new fixture + integration test in the same PR. Enforced via PR template checklist.
- **Major bumps are rare** — aim for one per quarter at most. If they happen monthly, the schema design needs rethinking, not more migrations.

### 3.3 Tool Layer (MCP)

Every infrastructure system is wrapped in an MCP server. Agents never call raw clients.

**Why MCP and not direct clients:**

- **Reusable**: Anyone with an MCP-compatible client (Claude Desktop, IDE plugins, other agents) can use our k8s tools.
- **Pluggable**: A user with Datadog can swap our Prom MCP server for a Datadog MCP server without touching agent code (Phase 2+ — see §3.3.1).
- **Auditable**: All tool invocations flow through a uniform protocol — single place to log, rate-limit, and enforce RBAC.

Servers are deployed as **separate pods** behind the main API. Each is independently scalable and replaceable.

#### 3.3.1 Why we ship our own MCP servers (Phase 1) and how that evolves (Phase 2+)

Community / vendor MCP servers exist for most systems we integrate — `kubernetes-mcp-server`, `prometheus-mcp-server`, Grafana's official LGTM MCP server, etc. The legitimate question: *why aren't we just composing those?*

We ship our own for Phase 1 because four properties of the platform can't be delegated to a third party without losing them:

| # | Property | What we'd lose by using a community server |
|---|---|---|
| 1 | **Curated, summarized response shapes** | Community k8s servers return raw API objects. Our `PodSummary` derives `status_reason: "CrashLoopBackOff"` from container state inspection — pushing that derivation to the LLM wastes tokens and degrades accuracy. |
| 2 | **Workload-agnostic semantics** | Our `search_exceptions` (in `mcp-loki`) matches Java/Python/Node/Go/.NET exception patterns in one call. Community Loki servers expose raw LogQL and force the agent to do runtime detection itself. |
| 3 | **Architectural read-only guarantee** | Community k8s servers ship with write tools (`apply`, `delete`, `scale`). They expect *you* to restrict via RBAC. We restrict at **both** layers: server only exposes read tools, ClusterRole only grants read verbs, and `test_rbac.py` enforces the latter. Composing security through a third-party tool surface is harder to guarantee. |
| 4 | **Sensitivity-aware tool shapes** | Our `get_configmap` returns *keys only*, not values, to avoid credentials-adjacent data leaking into traces. We do not expose `get_secret` at all. These are deliberate security postures we can't enforce on someone else's surface. |

**Phase 2+ direction — pluggable adapter pattern.** We commit to making the orchestrator agnostic to *which* MCP server implements a capability. Agents declare a *capability* (e.g. `find_pod`, `query_metrics`, `search_exceptions`); the adapter routes to whichever installed server provides it. This lets users:

- Replace our `mcp-prom` / `mcp-loki` / `mcp-tempo` with the **official Grafana MCP server** (one server, three signals)
- Plug in their existing Datadog / New Relic / ELK MCP servers behind our agents
- Add community MCP servers for adjacent systems (Cilium, Istio, ArgoCD, etc.) without modifying core code

Our own MCP servers remain as the **reference implementation** that ships with the default Helm install and guarantees the four properties above out-of-the-box. They become *optional* in Phase 2, not removed.

This decision is tracked in [ROADMAP.md Phase 2](roadmap.md#phase-2--production-ready-analysis) under "Observability adapter pattern."

### 3.4 Integration Layer

Direct integrations *inside* MCP servers (not exposed to agents):

- Kubernetes Python client → K8s MCP server
- Prometheus HTTP API → Prom MCP server
- Loki HTTP API → Loki MCP server
- Tempo HTTP API → Tempo MCP server
- Jenkins / GitHub Actions / ArgoCD APIs → CI MCP server (Phase 2)

### 3.5 Persistence Layer

| Store | Purpose | Phase |
|---|---|---|
| **Postgres** | Incident records, RCA reports, audit logs, LangGraph checkpoints | 1 |
| **pgvector** (Postgres extension) | Embedding store for long-term memory + RAG over prior incidents | 2 |
| **Redis** | Short-term cache (k8s API responses, recent metric queries), session state, rate limiting | 1 |

### 3.6 LLM Provider Layer

A thin abstraction over LangChain's chat models — but with **explicit provider configuration**, not env-var sniffing.

```text
LLMProvider (interface)
   ├─ AnthropicProvider     (BYOK)
   ├─ OpenAIProvider        (BYOK)
   ├─ BedrockProvider       (BYOK, AWS-native)
   ├─ AzureOpenAIProvider   (BYOK, enterprise)
   ├─ OllamaProvider        (local, air-gapped)
   └─ VLLMProvider          (local, high-throughput)
```

Each agent declares a *role* (e.g. `routing`, `analysis`, `summarization`) and the LLM router picks a model per role from config. This allows running a cheap model for routing and a strong model for RCA.

---

## 4. Agent Catalog

| Agent | Phase | Inputs | Outputs | Tools (MCP) |
|---|---|---|---|---|
| **Supervisor** | 1 | User query, state | Routing decisions, final report | — (orchestrates only) |
| **Kubernetes** | 1 | Service/namespace name | Pod states, events, deploy status | K8s MCP |
| **Metrics** | 1 | Service, time window | CPU/Mem/Net/Error-rate series, anomalies | Prom MCP |
| **Logs** | 1 | Service, time window | Errors, exceptions, patterns | Loki MCP |
| **Tracing** | 2 | Trace IDs, service | Latency hotspots, failed spans, dependency map | Tempo MCP |
| **Deployment** | 2 | Service | Recent deploys, commits, pipeline status | CI MCP |
| **RCA** | 1 | All sub-agent evidence | `{root_cause, confidence, evidence[], recommendations[]}` | — (reasoning only) |
| **Recommendation** | 1 | RCA output | Ranked, actionable suggestions (no execution) | — |
| **Remediation** | 4 | Approved plan | Audited per-action execution records | `mcp-k8s-write` (separate server, curated tools) |

Each agent has:
- A **system prompt** in `prompts/<agent>.md` (version-controlled, not inline)
- A **tool allowlist** (which MCP tools it may call)
- A **token budget** and **timeout**
- A **structured output schema** validated before returning to supervisor

---

## 5. End-to-End Data Flow

Walkthrough of a typical Phase 1 investigation: *"Why is `payment-service` failing?"*

```text
1. User submits query via Web UI
   → POST /investigations  { query, service: "payment-service", namespace: "prod" }

2. API Gateway authenticates, creates incident_id, persists to Postgres

3. Supervisor node starts the LangGraph workflow
   - parses query → identifies target service + signals needed
   - schedules K8s, Metrics, Logs agents in parallel

4. K8s Agent (via K8s MCP)
   - list_pods(namespace=prod, label=app=payment-service)
   - describe_pod(...) for each
   - get_events(namespace=prod, related_to=<pod>)
   - returns: {pod_states, restart_count, exit_codes, events}

5. Metrics Agent (via Prom MCP)
   - query_prometheus(rate(container_memory_usage_bytes[5m]))
   - query_prometheus(rate(http_requests_total{status=~"5.."}[5m]))
   - returns: {memory_trend, error_rate_trend, anomalies}

6. Logs Agent (via Loki MCP)
   - query_logs({service=payment-service, severity=error, last=15m})
   - search_exceptions(...)
   - returns: {error_clusters, exception_types, sample_lines}

7. Supervisor waits on all three (LangGraph barrier)

8. RCA Agent receives consolidated evidence
   - reasons over signals using LLM (role=analysis)
   - produces structured RCA output (schema-validated)
   - confidence score derived from signal corroboration

9. Recommendation Agent
   - takes RCA + cluster context → ranked remediation suggestions
   - NEVER executes (Phase 1)

10. Supervisor writes final report to Postgres, streams result to UI
    - emits OTel-GenAI trace to LangSmith + Phoenix
    - records token cost + latency
```

All steps run through LangGraph's checkpointer — if a pod restarts mid-investigation, the workflow resumes from the last completed node.

---

## 6. MCP Architecture (Detailed)

Each MCP server is a **separate FastAPI service** exposing the MCP protocol over HTTP+SSE.

### 6.1 Common Contract

```text
MCP Server
  ├─ /mcp/tools         GET  → list of tools w/ JSON schemas
  ├─ /mcp/invoke        POST → execute a tool call
  ├─ /mcp/health        GET  → liveness
  └─ /mcp/auth          POST → service-account token validation
```

### 6.2 Server-by-Server

| Server | Tools (Phase 1) | Backing Auth |
|---|---|---|
| **k8s-mcp** | `list_pods`, `describe_pod`, `get_events`, `get_nodes`, `get_deployments`, `get_services`, `get_pvcs`, `get_configmap` (read-only) | k8s ServiceAccount, RBAC: read-only ClusterRole or namespace-scoped Role |
| **prom-mcp** | `query_metrics`, `query_range`, `list_targets`, `query_alerts` | Bearer token to Prometheus (configurable) |
| **loki-mcp** | `query_logs`, `search_errors`, `search_exceptions`, `query_range_logs` | Bearer token to Loki (configurable) |
| **tempo-mcp** (P2) | `query_traces`, `get_trace`, `find_failed_spans`, `service_dependency_map` | Bearer token to Tempo |
| **ci-mcp** (P2) | `get_deployment_history`, `get_recent_commits`, `get_pipeline_status` | Pluggable: Jenkins API token / GHA PAT / ArgoCD token |

### 6.3 RBAC Model

The `k8s-mcp` server runs with a **dedicated ServiceAccount** that the Helm chart binds to a read-only ClusterRole by default (configurable to namespace-scoped Role for stricter installs). The MCP server never writes — even in Phase 4, write capability is provided by a **separate** `k8s-write-mcp` deployed only when remediation is enabled.

---

## 7. Memory Architecture

### 7.1 Short-Term Memory (Phase 1)

- **Mechanism**: LangGraph PostgresSaver checkpointer.
- **Scope**: A single investigation's state — agent outputs, evidence chain, messages, intermediate reasoning.
- **Lifetime**: Until the investigation concludes; then archived to the incidents table.

### 7.2 Long-Term Memory (Phase 2)

- **Mechanism**: pgvector-backed semantic store.
- **Stored items**:
  - Past incidents (full RCA report + outcome)
  - Cluster knowledge (service ownership, runbooks, known issues)
  - Resolution history (what fix worked for what symptom)
- **Retrieval**: Hybrid search — BM25 on metadata fields + dense vector similarity on incident summaries. Re-ranked before injection into RCA prompt.
- **Use during investigation**: Before the RCA agent reasons, it queries memory: *"Have we seen this combination of signals before?"* Past incidents become RAG context.

### 7.3 Cluster Knowledge Base (Phase 3)

Knowledge graph (Postgres tables + pgvector embeddings) representing:
- Services → Owners → Dependencies → SLOs
- Common failure modes per language/runtime (Java OOM patterns, Node event loop, etc.)
- Historical alert → root-cause mappings

---

## 8. Security Model

### 8.1 Authentication

| Surface | Mechanism |
|---|---|
| Web UI → API | OAuth2 / OIDC (Keycloak by default; pluggable) |
| API → MCP servers | Mutual mTLS within the cluster |
| MCP servers → k8s API | ServiceAccount + RBAC |
| MCP servers → Prom/Loki/Tempo | Bearer tokens via k8s Secrets |
| Agents → LLM provider | BYOK from k8s Secrets |

### 8.2 Authorization

- **RBAC**: User roles (`viewer`, `investigator`, `operator`, `admin`) gate Web UI actions.
- **Namespace scoping**: Investigations can be restricted to a namespace allowlist per user.
- **Tool allowlists**: Each agent declares which MCP tools it may invoke; supervisor enforces.

### 8.3 Agent Safety (Phase 1)

- Read-only by enforcement, not just by convention. The k8s ServiceAccount cannot create/update/delete *anything*. Helm chart RBAC is the gate.
- All LLM outputs are schema-validated before downstream use.
- Prompt-injection defense (Phase 2+): outputs containing tool-call patterns are sanitized before being shown back to LLMs in subsequent turns.

### 8.4 Audit

- Every tool invocation, LLM call, and user action is logged to a tamper-evident audit table.
- Exported to user's existing SIEM via OpenTelemetry.

---

## 9. AgentOps (Observability of KubePilot itself)

KubePilot is itself a production system running agents. We must observe it the same way we'd want users to observe their own agents.

| Concern | Tool | Notes |
|---|---|---|
| LLM traces (prompts, completions, tool calls) | **LangSmith** (BYO key) + **Phoenix** (self-hosted alternative) | Both supported; Phoenix is the default for air-gapped deployments. |
| Token cost / consumption | LangSmith + Postgres ledger | Per-investigation, per-tenant, per-model breakdown. |
| Agent latency | OpenTelemetry-GenAI semantic conventions | Exported to user's Grafana Tempo. |
| Tool-call success/failure rates | Prometheus metrics on MCP servers | Standard `/metrics` endpoint per server. |
| Eval scores | LangSmith datasets + offline harness | Golden RCA scenarios run on PR / nightly. |
| Drift detection (Phase 3) | DeepEval custom checks | Alert when RCA accuracy drops below threshold. |

---

## 10. Deployment Topology

### 10.1 Single Helm Chart

```text
kubepilot-ai/
├── charts/
│   ├── api-gateway/         (FastAPI)
│   ├── orchestrator/        (LangGraph runtime)
│   ├── mcp-k8s/
│   ├── mcp-prom/
│   ├── mcp-loki/
│   ├── mcp-tempo/           (P2)
│   ├── mcp-ci/              (P2)
│   ├── web-ui/              (Next.js)
│   ├── postgres/            (dependency: bitnami)
│   ├── redis/               (dependency: bitnami)
│   ├── phoenix/             (optional, for AgentOps)
│   └── ollama/              (optional, for air-gapped LLM)
└── values.yaml              (LLM provider config, RBAC scope, obs endpoints)
```

### 10.2 Reference Install Sizes

| Profile | Use case | Resource footprint |
|---|---|---|
| `dev` | Laptop kind/minikube | ~1.5 GB RAM, 1 vCPU |
| `prod-small` | Single-cluster SRE team | ~6 GB RAM, 3 vCPU |
| `prod-air-gapped` | Adds Ollama + Phoenix | +12 GB RAM (model-dependent), +1 GPU optional |

### 10.3 Required Cluster Permissions

Default Helm install creates:
- Namespace `kubepilot-system`
- ClusterRole `kubepilot-reader` (read-only across cluster)
- ClusterRoleBinding to the `k8s-mcp` ServiceAccount

Users may restrict to namespace-scoped Role via `values.yaml`.

---

## 11. Tech Stack Summary

| Layer | Choice | Rationale |
|---|---|---|
| Agent framework | LangGraph (+ LangChain) | State machines, checkpointing, parallel branches, large ecosystem |
| Tool protocol | MCP (Model Context Protocol) | Standard emerging across Anthropic/OpenAI/IDEs; reusable beyond KubePilot |
| Backend | Python 3.12 + FastAPI | Best agent ecosystem; matches user's Python skill |
| Frontend | Next.js + TypeScript + TailwindCSS + ShadCN | Modern, fast to build, good DX |
| Database | Postgres 16 + pgvector | One database for relational + vector; ops-friendly |
| Cache | Redis | Standard cluster cache |
| Container runtime | Docker, Kubernetes | The platform we're operating on |
| Deployment | Helm 3 | Standard k8s packaging |
| AgentOps | LangSmith (cloud) / Phoenix (self-hosted) | Both supported; Phoenix is default for air-gapped |
| Local LLM | Ollama, vLLM | Ollama for laptop/small; vLLM for production GPU inference |
| Eval | LangSmith Datasets + DeepEval | Golden scenarios + custom metrics |

---

## 12. Out-of-Scope Architecture (Captured for Clarity)

These items are explicitly **not** designed for in this document. If/when they become in-scope, they will be added in a versioned update:

- Multi-cluster federation (one KubePilot watching N clusters)
- SaaS control plane
- Mobile UI
- Bring-your-own observability stack (Datadog, New Relic, ELK, Splunk) — adapter pattern is acknowledged as a Phase 2+ design but not specified here
- Multi-tenancy beyond namespace scoping (full hard-tenant isolation)

---

## 13. Resolved Architecture Decisions

The five open questions raised during initial design have been resolved:

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | Streaming protocol from API → UI | **SSE (Server-Sent Events)** | Simpler than WebSocket, one-directional (server → client) matches our use case, native EventSource API in browsers, plays well with Next.js. Reconnection is automatic. |
| 2 | LangGraph state schema versioning | **Pydantic State + embedded `schema_version` + additive-only by default + migration registry for major bumps + fixture-replay CI test.** Full pattern in [§3.2.1](#321-state-schema--versioning). | Boring is correct for persistence layers. Mirrors how event-sourcing systems handle the same problem. Zero migration work for the common additive case. |
| 3 | MCP server discovery | **Kubernetes-native ServiceDiscovery.** Each MCP server is a `Service` in the `kubepilot-system` namespace. Orchestrator resolves by service DNS (`mcp-k8s.kubepilot-system.svc.cluster.local`). | Already in the platform we run on. No new config surface. Operators get standard k8s tooling for debugging discovery. Bonus: enables horizontal scaling per MCP server later. |
| 4 | Phoenix vs LangSmith default | **Phoenix is the default**, self-hosted via Helm subchart. LangSmith is optional and BYO key. | LangSmith requires a cloud account and outbound network — incompatible with air-gapped deployments. Phoenix is OSS, self-hosted, and OTel-GenAI compatible. Users can enable LangSmith in `values.yaml` if they prefer. |
| 5 | GPU scheduling for vLLM | **Leave to operator.** We document required GPU resource requests/limits and recommended NodeSelector/Affinity patterns, but do **not** ship opinionated scheduling rules. | GPU topology varies enormously across clusters (which node groups are GPU-enabled, taints, MIG splits, multi-tenancy rules). Shipping defaults would break more clusters than it helps. Documentation > magic. |

These decisions are now binding for Phase 1 implementation. Further architecture questions should be filed as GitHub issues against the initialized repository.
