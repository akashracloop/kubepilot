# Tracing & Deployment — the two Phase 2 specialists

> Phase 2 adds two new investigation surfaces to the [Phase 1](./PHASE_1_PLAN.md)
> stack (k8s + Prometheus + Loki): **distributed traces** via `mcp-tempo` and
> **deployment / CI-CD history** via `mcp-ci`. Each is a specialist sub-agent
> backed by its own MCP server, on the same REST contract as the Phase 1 servers
> ([ARCHITECTURE.md §6.1](./ARCHITECTURE.md#61-common-contract)). Both are
> **read-only** and **off by default** — turning them on in Helm lights up the
> corresponding branches of the LangGraph automatically.

---

## 1. What they add to an investigation

| Signal | Server | Agent | Answers |
|---|---|---|---|
| Distributed traces | `mcp-tempo` | Tracing specialist | *Where is the latency? Which span is failing? What does this service depend on?* |
| Deployments / CI-CD | `mcp-ci` | Deployment specialist | *Did a deploy land just before this incident? What changed? Is the pipeline healthy?* |

Both specialists run **in parallel** with the K8s / Metrics / Logs agents and
fan in to the RCA agent, which correlates all five signal types. Like every
KubePilot agent they **observe and report** — they emit `Evidence`, they do not
diagnose root cause and never write anything.

---

## 2. `mcp-tempo` — the Tempo MCP server

`mcp-tempo` wraps the Grafana Tempo HTTP API and returns **curated** span views
rather than raw OTLP span batches. An agent reasons about a compact
`TraceSummary` (root duration, span/error counts, slowest span) far more cheaply
and accurately than about a raw span tree — the same token-efficiency argument
as `mcp-k8s`'s `PodSummary`
([ARCHITECTURE.md §3.3.1](./ARCHITECTURE.md#331-why-we-ship-our-own-mcp-servers-phase-1-and-how-that-evolves-phase-2)).

### 2.1 Tools

| Tool | Signature | Returns | Use |
|---|---|---|---|
| `query_traces` | `query_traces(service, start?, end?, tags?, limit=20)` | `list[TraceSummary]` | Find slow or failing requests. `start`/`end` are RFC3339 (default: last hour); `tags` narrows the search, e.g. `{"http.status_code": "500"}`. |
| `get_trace` | `get_trace(trace_id)` | `TraceDetail` | Drill into one trace — all spans with timing and status. |
| `find_failed_spans` | `find_failed_spans(service, window_minutes=15)` | `FailedSpansResult` | Pinpoint which operations are erroring over a recent window. |
| `service_dependency_map` | `service_dependency_map(service, window_minutes=60)` | `DependencyMap` | Immediate upstream/downstream edges (caller, callee, call count, error count, p99), from Tempo's service-graph metrics. |

Curated response shapes (`mcp_tempo.models`): `TraceSummary`, `TraceDetail`,
`SpanRef`, `DependencyEdge`, `DependencyMap`, `FailedSpansResult`. Span status is
collapsed onto a three-value enum — `ok` | `error` | `unset` — so agents never
reason about raw OTLP status constants.

### 2.2 Upstream (Tempo) configuration

The server reads its Tempo endpoint from the environment:

| Env var | Default | Purpose |
|---|---|---|
| `KUBEPILOT_TEMPO_URL` | `http://localhost:3200` | Tempo HTTP API base URL |
| `KUBEPILOT_TEMPO_TOKEN` | *(unset)* | Optional bearer token, sent as `Authorization: Bearer …` |

Everything is served on the standard MCP surface (`/mcp/tools`, `/mcp/invoke`,
`/mcp/health`).

---

## 3. `mcp-ci` — the Deployment / CI-CD MCP server

`mcp-ci` exposes **one tool surface with three interchangeable backends** behind
it. The agent is backend-agnostic: each adapter normalizes its provider's wire
format into the same curated models.

### 3.1 Tools

| Tool | Signature | Returns | Use |
|---|---|---|---|
| `get_deployment_history` | `get_deployment_history(service, window_minutes=60)` | `DeploymentHistory` | List recent deploys (version, timestamp, status). Check whether a deploy landed just before the incident window. |
| `get_recent_commits` | `get_recent_commits(repo, window_minutes=60)` | `CommitList` | Recent commits (sha, message, author, timestamp) to tie an incident to a code change. |
| `get_pipeline_status` | `get_pipeline_status(repo_or_service)` | `PipelineStatus` | Latest pipeline/build status — is the delivery pipeline itself healthy? |

Curated shapes (`mcp_ci.models`): `Deployment`, `DeploymentHistory`, `Commit`,
`CommitList`, `PipelineStatus`. Deployment and pipeline statuses are normalized
onto a shared vocabulary — `succeeded` | `failed` | `in_progress` — regardless
of backend.

### 3.2 Pluggable backend

The active backend is selected by config; all three implement the same read-only
contract (`deployment_history`, `recent_commits`, `pipeline_status`).

| Env var | Default | Values / meaning |
|---|---|---|
| `KUBEPILOT_CI_BACKEND` | `github_actions` | `github_actions` \| `jenkins` \| `argocd` |
| `KUBEPILOT_CI_URL` | backend default* | API base URL |
| `KUBEPILOT_CI_TOKEN` | *(unset)* | Read-only API token / PAT, sent as `Authorization: Bearer …` |

\* When `KUBEPILOT_CI_URL` is unset the backend falls back to its default base
URL: `https://api.github.com` for `github_actions`, `http://localhost:8080` for
`jenkins` and `argocd`.

**What the `service` / `repo` identifier means depends on the backend:**

| Backend | Identifier shape | Example |
|---|---|---|
| `github_actions` | `owner/name` repo slug | `acme/checkout-service` |
| `jenkins` | Job name | `checkout-service-deploy` |
| `argocd` | Application name | `checkout` |

---

## 4. The specialist agents

Both agents are thin shells over the shared tool-loop runner
(`agents/_runner.py`); their behavior lives in version-controlled prompts
(`prompts/tracing_agent.md`, `prompts/deployment_agent.md`), not inline code.

**Tracing specialist** (`agents/tracing_agent.py`, default window 15 min) emits
`Evidence` of kind `latency_hotspot`, `failed_span`, `dependency_edge`, or
`trace_summary`. Traces are often sparse — if none exist for the service/window
it records a single `trace_summary` (severity `info`) and stops. Missing traces
are **not** an error.

**Deployment specialist** (`agents/deployment_agent.py`, default window 60 min)
emits `Evidence` of kind `recent_deploy`, `recent_commit`, or `pipeline_status`.
A deploy that closely **precedes** the incident is flagged `warning`; routine
history is `info`. The agent notes the temporal correlation and lets the RCA
agent weigh it — it does not assert the deploy *caused* the incident. Absent CI
data (no configured backend, or no recent activity) is likewise not an error.

---

## 5. Enabling them in Helm

Both servers are **disabled by default**. The chart's `mcp.tempo` / `mcp.ci`
blocks (`charts/kubepilot-ai/values.yaml`):

```yaml
mcp:
  # ... k8s / prom / loki (Phase 1, enabled) ...

  # Phase 2 — off by default; enable when Tempo / a CI backend is available.
  # When enabled, the gateway adds the Tracing / Deployment specialist branches.
  tempo:
    enabled: false
    upstream:
      url: http://tempo.observability.svc.cluster.local:3200
      bearerTokenSecretRef: ""   # optional Secret (key: token) with a bearer token
  ci:
    enabled: false
    backend: github_actions       # github_actions | jenkins | argocd
    url: ""                       # backend API base URL
    tokenSecretRef: ""            # Secret (key: token) with a read-only CI token
```

Turn them on at install/upgrade:

```bash
helm upgrade --install kubepilot-ai ./charts/kubepilot-ai \
  --namespace kubepilot-system \
  -f charts/kubepilot-ai/values-prod-small.yaml \
  --set mcp.tempo.enabled=true \
  --set mcp.tempo.upstream.url=http://tempo.observability.svc.cluster.local:3200 \
  --set mcp.ci.enabled=true \
  --set mcp.ci.backend=github_actions
```

For authenticated upstreams, create the token Secrets first and reference them:

```bash
kubectl -n kubepilot-system create secret generic tempo-bearer --from-literal=token=...
kubectl -n kubepilot-system create secret generic ci-token     --from-literal=token=ghp_...

helm upgrade --install kubepilot-ai ./charts/kubepilot-ai -n kubepilot-system \
  --set mcp.tempo.enabled=true --set mcp.tempo.upstream.bearerTokenSecretRef=tempo-bearer \
  --set mcp.ci.enabled=true    --set mcp.ci.tokenSecretRef=ci-token \
  --set mcp.ci.backend=github_actions
```

### 5.1 How enabling lights up the graph (automatic)

You do **not** wire the agents in by hand. The plumbing is:

1. `mcp.tempo.enabled` / `mcp.ci.enabled` render the `mcp-tempo` / `mcp-ci`
   Deployments **and** inject the gateway env vars
   `KUBEPILOT_API_MCP__TEMPO` / `KUBEPILOT_API_MCP__CI`
   (pointing at the in-cluster `mcp-tempo` / `mcp-ci` Services on port 8080).
2. The gateway's MCP endpoint settings default those two to the **empty string**
   (`services/api-gateway/src/kubepilot_api/config.py` → `MCPEndpoints.tempo` /
   `.ci`). An empty endpoint means "server not deployed."
3. When an endpoint is non-empty, the gateway registers the `TRACING` /
   `DEPLOYMENT` capability with the router and passes a live `mcp_tempo` /
   `mcp_ci` client into `AgentDeps`; the graph adds the matching specialist
   branch. When it's empty, that branch is simply omitted.

So the only action to add trace/deploy analysis is flipping the two Helm flags
(and supplying upstreams). No orchestrator config changes.

> Both servers run with the same hardened, read-only pod posture as the Phase 1
> MCP servers (`runAsNonRoot`, `readOnlyRootFilesystem`, all capabilities
> dropped) and expose only read tools.

---

## 6. Local development

Run either server directly with `uv` (they listen on their own ports; point the
gateway at them via `KUBEPILOT_API_MCP__TEMPO` / `__CI`):

```bash
# Tempo MCP
export KUBEPILOT_TEMPO_URL=http://localhost:3200
# export KUBEPILOT_TEMPO_TOKEN=...            # if your Tempo needs auth
uv run --package kubepilot-mcp-tempo uvicorn mcp_tempo.server:app --port 8084

# CI MCP (GitHub Actions backend)
export KUBEPILOT_CI_BACKEND=github_actions
export KUBEPILOT_CI_TOKEN=ghp_...             # read-only PAT
uv run --package kubepilot-mcp-ci uvicorn mcp_ci.server:app --port 8085

# Point the gateway at them
export KUBEPILOT_API_MCP__TEMPO=http://localhost:8084
export KUBEPILOT_API_MCP__CI=http://localhost:8085
```

Probe a server directly:

```bash
curl -s localhost:8084/mcp/tools        # tempo tool descriptors + JSON schemas
curl -s localhost:8084/mcp/health       # {"status":"ok","server":"mcp-tempo",...}

curl -s -X POST localhost:8085/mcp/invoke \
  -H 'Content-Type: application/json' \
  -d '{"tool":"get_deployment_history","arguments":{"service":"acme/checkout-service","window_minutes":120}}'
```

---

## 7. Swapping the backends (adapter pattern)

Because the agents talk to **capabilities** (`tracing`, `deployment`), not to
specific servers, you can point the tracing capability at the **official Grafana
MCP server** (one server for metrics + logs + traces) config-only. See
[mcp-adapters.md](./mcp-adapters.md) for the capability model and a worked
Grafana example. A first-party vendor CI adapter beyond the three shipped
backends is a Phase 3 item.

## Next steps

- [Long-term memory](./memory.md) — how concluded incidents become RCA context
- [MCP adapters](./mcp-adapters.md) — swapping tool backends
- [Architecture](./ARCHITECTURE.md) — the engineering view
