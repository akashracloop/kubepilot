# Troubleshooting

> Concrete **symptom → cause → fix** for the issues you're most likely to hit in Phase 1. See also [install.md](install.md) and [llm-providers.md](../configuration/llm-providers.md).

Quick triage checklist:

```bash
make smoke-test                               # local: config + Postgres + Redis + LLM
curl -s localhost:8080/ready                  # cluster: gateway readiness (MCP + DB + LLM)
curl -s localhost:8081/mcp/health             # each MCP server: {"status":"ok",...}
kubectl -n kubepilot-system get pods          # everything Running/Ready?
kubectl -n kubepilot-system logs deploy/kubepilot-ai-orchestrator
```

---

## Provider not configured

**Symptom.** `ProviderNotConfigured` in logs or a failing `/ready`; investigations never produce an RCA. Messages like:
- `Provider 'anthropic' required by role analysis is not loaded`
- `No LLM binding configured for role=analysis`

**Cause.** A role in `llm.roles` names a provider whose credential/endpoint isn't set, or a role has no binding at all. The factory only loads providers referenced by a role and raises if the selected one has no credential.

**Fix.**
- Ensure every provider named across `llm.roles` has its credential set:
  - `anthropic` → `anthropic_api_key`
  - `openai` → `openai_api_key`
  - `azure` → `azure_api_key` **and** `azure_endpoint`
  - `bedrock` → `bedrock_region` (+ AWS creds via IRSA/instance role)
  - `ollama` / `vllm` → reachable base URL (no key)
- In Helm, confirm the `llm.secretName` Secret contains a key for **each** referenced provider (`kubectl -n kubepilot-system get secret kubepilot-llm-credentials -o jsonpath='{.data}'`).
- Locally, remember the service reads the prefixed form `KUBEPILOT_LLM__ANTHROPIC_API_KEY`, not just `ANTHROPIC_API_KEY`.

---

## MCP server connectivity

**Symptom.** Investigation stalls or the K8s/Metrics/Logs agent returns no evidence; gateway `/ready` reports an MCP server down; logs show connection-refused to `mcp-k8s` / `mcp-prom` / `mcp-loki`.

**Cause.** An MCP server pod isn't up, or the gateway's MCP endpoint config points at the wrong address.

**Fix.**
1. Hit each server's health endpoint directly:
   ```bash
   curl -s localhost:8081/mcp/health   # mcp-k8s
   curl -s localhost:8082/mcp/health   # mcp-prom
   curl -s localhost:8083/mcp/health   # mcp-loki
   ```
   A healthy server returns `{"status":"ok","server":"mcp-k8s","version":"..."}`. List its tools with `GET /mcp/health`'s sibling `GET /mcp/tools`.
2. Confirm the gateway's endpoint config matches:
   - Local: `KUBEPILOT_API_MCP__K8S`, `KUBEPILOT_API_MCP__PROM`, `KUBEPILOT_API_MCP__LOKI` (defaults `http://localhost:8081/8082/8083`).
   - Cluster: the MCP `Service` DNS names in `kubepilot-system`.
3. Check the pod: `kubectl -n kubepilot-system get pods -l app.kubernetes.io/component=mcp-k8s` and its logs.

> These servers speak a **REST-flavored** MCP contract (`GET /mcp/tools`, `POST /mcp/invoke`, `GET /mcp/health`) over HTTP — **not** stdio/SSE MCP. If you're testing with a stdio MCP client, that's the wrong transport for Phase 1.

---

## Postgres / Redis connection

**Symptom.** `make smoke-test` reports `postgres_failed` or `redis_failed`; the gateway/orchestrator crash-loops on startup; SSE never streams because state can't persist.

**Cause.** The DB/cache isn't running or the URL is wrong.

**Fix.**
- Local: run `make dev-up` and wait for `Postgres ready.` The compose stack is `pgvector/pgvector:pg16` (Postgres) + `redis:7-alpine`. Verify with `docker compose ps`.
- Check the connection URLs:
  - Orchestrator: `KUBEPILOT_DB__URL` (default `postgresql://kubepilot:kubepilot@localhost:5432/kubepilot`), `KUBEPILOT_REDIS__URL` (default `redis://localhost:6379/0`).
  - Gateway: `KUBEPILOT_API_DB__URL`.
- Cluster: if you set `postgres.enabled=false` / `redis.enabled=false`, you **must** provide `postgres.externalUrl` / `redis.externalUrl` pointing at your own instances.
- Wiped state? `make dev-reset` recreates volumes (destructive).

---

## RBAC / permission errors from the k8s MCP

**Symptom.** The Kubernetes agent returns errors like `pods is forbidden: User "system:serviceaccount:kubepilot-system:...-mcp-k8s" cannot list resource "pods"` — often only for certain namespaces.

**Cause.** The bound Role/ClusterRole doesn't cover the namespace or resource you're investigating. This is **expected** when `mcp.k8s.rbac.scope: namespace` and the target namespace isn't in the allowlist.

**Fix.**
- Cluster-wide read: set `mcp.k8s.rbac.scope: cluster` (binds the read-only `...-mcp-k8s-reader` ClusterRole).
- Namespace-scoped: add the namespace to `mcp.k8s.rbac.namespaces` and upgrade the release. An empty list with `scope: namespace` fails the render deliberately.
- Verify what's granted:
  ```bash
  kubectl get clusterrole kubepilot-ai-mcp-k8s-reader -o yaml
  ```

**This is not a bug to "fix" by adding write verbs.** The verb set is fixed to `get`/`list`/`watch` by design and enforced by `services/mcp-k8s/tests/test_rbac.py`. There is intentionally **no `get_secret` tool**, and `get_configmap` returns **keys only, never values** — a permission error on Secrets is the read-only guarantee working as intended ([ARCHITECTURE.md §8.3](../reference/architecture.md#83-agent-safety-phase-1)).

---

## Empty or low-confidence RCA

**Symptom.** The RCA report has a vague root cause, `confidence` well below your expectation, or an empty evidence list.

**Cause (in order of likelihood).**
1. **Under-powered analysis model** — the biggest driver. RCA quality lives in the `analysis` role.
2. **Missing signals** — Prometheus/Loki upstreams unreachable or empty for the queried time window, so the RCA agent has little to correlate.
3. **Wrong scope** — the `service` / `namespace` / `time_window_minutes` in the request don't line up with where the failure actually is.

**Fix.**
- Point the `analysis` role at your strongest model (Sonnet/Opus, gpt-4o, or a 14B+ local model). Don't run RCA on a routing-tier model.
- Confirm evidence is actually flowing: check that the Metrics and Logs agents returned data (SSE stream or the investigation record's `state`). If they're empty, fix the Prom/Loki MCP upstreams first.
- Widen `time_window_minutes` (1–1440) if the incident predates the default 30-minute window.
- Confidence is derived from **signal corroboration** — a single weak signal legitimately yields low confidence. That's the system being honest, not broken.

---

## Air-gapped model quality

**Symptom.** RCA is consistently shallow or low-confidence, but only on an air-gapped (Ollama/vLLM) install.

**Cause.** The analysis-role model is too small. Small local models can't hold enough of the correlated evidence to reason well.

**Fix.**
- Use **14B+ for the analysis role** (e.g. `qwen2.5:14b` via vLLM); keep `routing`/`summarization` on a fast 8B model.
- Make sure the model is actually pulled/served (`ollama pull qwen2.5:14b`; confirm the vLLM endpoint serves the named model).
- Verify `llm.ollama.baseUrl` / `vllm_base_url` resolve from inside the cluster.
- See [llm-providers.md §6](../configuration/llm-providers.md#6-air-gapped--ollama--vllm).

---

## SSE stream not updating

**Symptom.** `GET /investigations/{id}/stream` connects but no events arrive, or the web UI live view stays blank while the investigation is clearly running.

**Cause.** A proxy/ingress buffering the event stream, an already-finished investigation (nothing left to stream), or the orchestrator not publishing progress (often a downstream failure — DB/MCP/LLM).

**Fix.**
- Test the raw stream with `curl -N` (disables buffering): `curl -N localhost:8080/investigations/<id>/stream -H "X-API-Key: $KEY"`.
- If a reverse proxy/ingress sits in front, disable response buffering for the stream path (e.g. nginx `proxy_buffering off;`). SSE was chosen precisely for its simplicity ([ARCHITECTURE.md §13, decision 1](../reference/architecture.md#13-resolved-architecture-decisions)) — buffering is the usual culprit.
- If the investigation already completed, fetch the final snapshot instead: `GET /investigations/{id}`.
- No progress at all? Check orchestrator logs — a stalled investigation usually means an upstream (MCP/LLM/DB) failure, not an SSE problem.

---

## Auth 401s

**Symptom.** `401 Unauthorized`, body `{"detail":"Invalid or missing API key"}`, `WWW-Authenticate: X-API-Key`.

**Cause.** The gateway has an API key configured and your request didn't send a matching `X-API-Key` header.

**Fix.**
- Send the header: `-H "X-API-Key: $API_KEY"`.
- Retrieve the configured key:
  ```bash
  kubectl -n kubepilot-system get secret kubepilot-api-auth \
    -o jsonpath='{.data.api_key}' | base64 -d
  ```
- Header name is configurable via `KUBEPILOT_API_AUTH__API_KEY_HEADER` (default `X-API-Key`) — make sure client and server agree.
- **Getting 401 in local dev and don't want auth?** Leave `KUBEPILOT_API_AUTH__API_KEY` unset — auth then becomes a no-op and all requests pass. (Never do this on a shared/production install.)
- Key comparison is constant-time; a *slightly* wrong key fails exactly like a missing one.

> OIDC / Keycloak with roles is **Phase 3**. Phase 1 is intentionally a single static shared key.

---

## Still stuck?

- Turn up logs: `KUBEPILOT_LOG_LEVEL=DEBUG` (orchestrator) / `KUBEPILOT_API_LOG_LEVEL=DEBUG` (gateway).
- Reproduce locally against a kind cluster (`make kind-up`) to isolate cluster-specific issues.
- Open a GitHub issue with the failing `/ready` output, relevant pod logs, and your `llm.roles` config (redact keys).
</content>
