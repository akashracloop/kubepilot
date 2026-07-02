# Installing KubePilot AI

> How to run KubePilot AI two ways: **(A)** locally for development, and **(B)** into a Kubernetes cluster via Helm. Read [ARCHITECTURE.md](./ARCHITECTURE.md) for the system view and [llm-providers.md](./llm-providers.md) for provider configuration.

KubePilot AI is a **read-only** investigator in Phase 1 — it never writes to your cluster (see [ARCHITECTURE.md §8.3](./ARCHITECTURE.md#83-agent-safety-phase-1)). Everything below preserves that guarantee.

The Phase 1 build ships the API gateway, the LangGraph orchestrator, three MCP servers (`k8s`, `prom`, `loki`), the Next.js web UI, and the umbrella Helm chart with its application templates. All of it deploys from one `helm install`.

---

## Path A — Local Development

Best for hacking on the orchestrator, MCP servers, or gateway. Runs the Python services against a local Postgres + Redis (docker-compose) and, optionally, a local kind cluster with a Prometheus/Loki stack.

### A.1 Prerequisites

| Tool | Why | Install hint |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python packaging + workspace runner | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python 3.12 | Runtime (fetched by `uv` if missing) | — |
| Docker | Runs Postgres + Redis via docker-compose | Docker Desktop / Colima |
| [kind](https://kind.sigs.k8s.io/) | Local cluster for end-to-end work (optional) | `brew install kind` |
| `kubectl`, `helm` | Cluster + chart tooling (optional locally) | `brew install kubectl helm` |
| An LLM credential | At least one provider for real RCA | see [llm-providers.md](./llm-providers.md) |

### A.2 Quickstart

```bash
# 1. Install all workspace dependencies (creates .venv, fetches Python 3.12 if needed)
make install

# 2. Start local Postgres (pgvector/pgvector:pg16) + Redis
make dev-up

# 3. Point at an LLM provider (any one is enough for the smoke test)
export KUBEPILOT_LLM__ANTHROPIC_API_KEY=sk-ant-...
#   or:  export ANTHROPIC_API_KEY=sk-ant-...
#   or:  export OPENAI_API_KEY=sk-...            (and set default_provider=openai)
#   or:  run an Ollama server and set default_provider=ollama (no key needed)

# 4. Verify config + DB + Redis + LLM wiring
make smoke-test
```

A passing run prints `SMOKE TEST: PASSED`. The smoke test (`kubepilot_orch.smoke_test`) checks, in order:

1. Settings load (`OrchestratorSettings`)
2. Postgres reachable (connect only — no schema required yet)
3. Redis reachable
4. The **analysis-role** provider can complete a one-shot prompt — *skipped gracefully* if no credential is configured for that provider, so a missing key is not a hard failure in dev.

### A.3 Running the services

The docker-compose stack (`make dev-up`) only provides Postgres + Redis. Run the application services with `uv` from the workspace root:

```bash
# Orchestrator settings use the KUBEPILOT_ prefix; gateway uses KUBEPILOT_API_
export KUBEPILOT_LLM__DEFAULT_PROVIDER=anthropic
export KUBEPILOT_LLM__ANTHROPIC_API_KEY=sk-ant-...

# API gateway (FastAPI) — serves /investigations, /health, /ready
uv run --package kubepilot-api uvicorn kubepilot_api.main:app --port 8080

# MCP servers (each is its own FastAPI app; run in separate shells)
uv run --package kubepilot-mcp-k8s  uvicorn mcp_k8s.server:app  --port 8081
uv run --package kubepilot-mcp-prom uvicorn mcp_prom.server:app --port 8082
uv run --package kubepilot-mcp-loki uvicorn mcp_loki.server:app --port 8083
```

The gateway's default MCP endpoints (`http://localhost:8081/8082/8083`) match those ports — override with `KUBEPILOT_API_MCP__K8S`, `KUBEPILOT_API_MCP__PROM`, `KUBEPILOT_API_MCP__LOKI` if you change them.

The MCP servers read their upstreams from env vars:

```bash
export KUBEPILOT_KUBECONFIG=$HOME/.kube/config     # mcp-k8s (in-cluster uses the ServiceAccount)
export KUBEPILOT_PROMETHEUS_URL=http://localhost:9090
export KUBEPILOT_LOKI_URL=http://localhost:3100
# optional bearer tokens:
# export KUBEPILOT_PROMETHEUS_TOKEN=... ; export KUBEPILOT_LOKI_TOKEN=...
```

### A.4 A local cluster with signals (optional)

For end-to-end work you need a cluster with Prometheus + Loki. The helper script spins up a kind cluster and installs both:

```bash
make kind-up        # kind cluster 'kubepilot-dev' + Prometheus + Loki in namespace 'observability'
make kind-down      # tear it down

# or drive the script directly:
bash scripts/dev-cluster.sh up
bash scripts/dev-cluster.sh status
```

Switch context with `kubectl config use-context kind-kubepilot-dev`. Point `KUBEPILOT_PROMETHEUS_URL` / `KUBEPILOT_LOKI_URL` at the in-cluster services (or port-forward them).

### A.5 Running tests

```bash
make test               # unit tests only (excludes integration + live_llm markers)
make test-integration   # requires `make dev-up` (real Postgres/Redis)
make lint               # ruff check + format --check
make typecheck          # mypy (strict) over services/
make check              # lint + typecheck + tests
```

---

## Path B — Helm Install into a Cluster

The production distribution: one chart deploys the gateway, orchestrator, MCP servers, web UI, and (optionally) bundled Postgres/Redis/Phoenix/Ollama.

### B.1 Prerequisites

- A Kubernetes cluster (1.27+) and `kubectl` context pointing at it
- Helm 3
- Reachable Prometheus and Loki endpoints (Grafana LGTM stack — the only supported observability stack in Phase 1)
- An LLM credential (cloud BYOK) **or** a reachable local model endpoint (Ollama / vLLM) for air-gapped installs
- Cluster permission to create a namespace, a read-only ClusterRole, and a ClusterRoleBinding (see [ARCHITECTURE.md §10.3](./ARCHITECTURE.md#103-required-cluster-permissions))

### B.2 Install profiles

The chart ships three value profiles. Pick one as your base and layer your own overrides on top.

| Profile | Values file | Use case | Footprint |
|---|---|---|---|
| `dev` | `values-dev.yaml` | Laptop kind/minikube | ~1.5 GB RAM, 1 vCPU |
| `prod-small` | `values-prod-small.yaml` | Single-cluster SRE team | ~6 GB RAM, 3 vCPU |
| `prod-air-gapped` | `values-prod-air-gapped.yaml` | Disconnected cluster; adds Ollama + Phoenix | +12 GB RAM (model-dependent), optional GPU |

### B.3 Quickstart (cloud LLM, Anthropic default)

```bash
# 1. Create the namespace and the LLM credential Secret the chart references
kubectl create namespace kubepilot-system

kubectl -n kubepilot-system create secret generic kubepilot-llm-credentials \
  --from-literal=anthropic_api_key=sk-ant-...

# 2. Create the static API-key Secret used by the gateway (X-API-Key auth)
kubectl -n kubepilot-system create secret generic kubepilot-api-auth \
  --from-literal=api_key="$(openssl rand -hex 24)"

# 3. Install
helm install kubepilot-ai ./charts/kubepilot-ai \
  --namespace kubepilot-system \
  -f charts/kubepilot-ai/values-prod-small.yaml \
  --set mcp.prom.upstream.url=http://prometheus-server.observability.svc.cluster.local \
  --set mcp.loki.upstream.url=http://loki.observability.svc.cluster.local:3100
```

### B.4 Values you will almost always override

The default `values.yaml` is the canonical reference. The blocks you typically touch:

```yaml
# --- LLM provider (BYOK). Full guide: docs/llm-providers.md ---
llm:
  defaultProvider: anthropic
  roles:
    routing:       { provider: anthropic, model: claude-haiku-4-5-20251001 }
    analysis:      { provider: anthropic, model: claude-sonnet-4-6 }
    summarization: { provider: anthropic, model: claude-haiku-4-5-20251001 }
  secretName: kubepilot-llm-credentials   # holds anthropic_api_key / openai_api_key / etc.

# --- MCP upstreams: your Grafana LGTM endpoints ---
mcp:
  k8s:
    enabled: true
    rbac:
      scope: cluster            # cluster (read-only ClusterRole) | namespace
      namespaces: []            # required list when scope == "namespace"
  prom:
    enabled: true
    upstream:
      url: http://prometheus-server.observability.svc.cluster.local
      bearerTokenSecretRef: ""  # optional Secret ref for a bearer token
  loki:
    enabled: true
    upstream:
      url: http://loki.observability.svc.cluster.local:3100
      bearerTokenSecretRef: ""

# --- Persistence: bundled by default; set enabled=false to BYO ---
postgres:
  enabled: true
  externalUrl: ""               # used when enabled=false
redis:
  enabled: true
  externalUrl: ""

# --- AgentOps ---
agentOps:
  phoenix:
    enabled: true               # self-hosted default, OTel-GenAI compatible
  langsmith:
    enabled: false              # opt-in BYOK
    apiKeySecretRef: ""

# --- Remediation stays OFF (Phase 4) ---
remediation:
  enabled: false
```

**Tightening RBAC scope.** To restrict the k8s MCP to specific namespaces instead of a cluster-wide read-only role:

```yaml
mcp:
  k8s:
    rbac:
      scope: namespace
      namespaces: [prod, payments, checkout]
```

The chart renders a read-only `Role`/`RoleBinding` per namespace. The verb set is fixed to `get`/`list`/`watch` — write verbs cannot be added (asserted by `services/mcp-k8s/tests/test_rbac.py`).

### B.5 Air-gapped install

Use the air-gapped profile and route all roles to local models. No outbound network to `api.anthropic.com` is required.

```bash
helm install kubepilot-ai ./charts/kubepilot-ai \
  --namespace kubepilot-system \
  -f charts/kubepilot-ai/values-prod-air-gapped.yaml
```

That profile enables the bundled Ollama subchart and Phoenix, and sets the role bindings to Ollama/vLLM. See [llm-providers.md](./llm-providers.md#air-gapped-ollama--vllm) for the exact role configuration and model-size guidance.

### B.6 Verifying the install

```bash
# All pods Running / Ready
kubectl -n kubepilot-system get pods

# Gateway readiness (checks MCP servers, DB, LLM provider)
kubectl -n kubepilot-system port-forward svc/kubepilot-ai-api-gateway 8080:8080 &
curl -s localhost:8080/health   # liveness
curl -s localhost:8080/ready    # readiness

# Each MCP server exposes a health endpoint
kubectl -n kubepilot-system port-forward svc/kubepilot-ai-mcp-k8s 8081:8081 &
curl -s localhost:8081/mcp/health     # {"status":"ok","server":"mcp-k8s",...}
curl -s localhost:8081/mcp/tools      # list of read-only tools + JSON schemas
```

Confirm the read-only guarantee directly:

```bash
kubectl get clusterrole kubepilot-ai-mcp-k8s-reader -o yaml | grep -A2 verbs
# verbs are only: get / list / watch
```

### B.7 Trigger your first investigation

Investigations are started over the REST API with the static `X-API-Key` header (Phase 1 auth — see [ARCHITECTURE.md §8.1](./ARCHITECTURE.md#81-authentication)). Retrieve the key you created and POST an investigation:

```bash
API_KEY=$(kubectl -n kubepilot-system get secret kubepilot-api-auth \
  -o jsonpath='{.data.api_key}' | base64 -d)

curl -s -X POST localhost:8080/investigations \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "query": "Why is payment-service failing?",
        "namespace": "prod",
        "service": "payment-service",
        "time_window_minutes": 30
      }'
```

The gateway responds `202 Accepted` with the created record:

```json
{ "incident_id": "…uuid…", "status": "running", "created_at": "…" }
```

Poll the result, or stream live agent progress via SSE:

```bash
# Snapshot
curl -s localhost:8080/investigations/<incident_id> -H "X-API-Key: $API_KEY"

# Live SSE stream of agent progress
curl -N localhost:8080/investigations/<incident_id>/stream -H "X-API-Key: $API_KEY"
```

The web UI drives the same endpoints — open it (via its Service / Ingress) to trigger investigations and watch the streaming trace and RCA report card.

> **Auth note.** If `KUBEPILOT_API_AUTH__API_KEY` (Helm: the `kubepilot-api-auth` Secret) is unset, auth is **disabled** and all requests are accepted. That mode is for local dev only — always set a key for any shared or production install.

### B.8 Uninstall

```bash
helm uninstall kubepilot-ai -n kubepilot-system
kubectl delete namespace kubepilot-system   # if you want the Secrets/PVCs gone too
```

---

## Next steps

- [Configure LLM providers](./llm-providers.md) — all six providers, per-role routing, air-gapped setup
- [Troubleshooting](./troubleshooting.md) — provider errors, MCP connectivity, RBAC, low-confidence RCA
- [Architecture](./ARCHITECTURE.md) — the engineering view
</content>
</invoke>
