# KubePilot AI — Phase 2 Implementation Plan

> **Goal:** Move from "read-only tech demo" to "a team uses this daily." Add the
> two missing investigation surfaces (traces, deployments), give the agent
> **memory** of past incidents, generate incident **timelines**, and meet users
> where they live (**Slack**, **CLI**) — while making the MCP tool layer
> **pluggable** so users can bring their own observability backends.

> Reference: [ARCHITECTURE.md](architecture.md) (the *what*), this doc (the
> *how* for v0.2.x), [ROADMAP.md](roadmap.md) (the *when across phases*),
> [PHASE_1_PLAN.md](phase-1-plan.md) (what shipped in v0.1.x and is assumed
> here). **Do not start Phase 2 until Phase 1 is tagged and demoed.**

---

## 0. What Phase 1 Gives Us (Starting Point)

Phase 2 builds on real seams, not a blank slate. The pieces below already exist
and are the extension points for this phase:

| Phase 1 artifact | Phase 2 extends it by… |
|---|---|
| `InvestigationState` (Pydantic, `schema_version`, additive-only discipline — ARCHITECTURE §3.2.1) | Adding `memory_context`, `timeline`, and trace/deploy evidence as **additive** fields (schema v1 → v2, fixture-replay test) |
| `MCPClient` + REST contract (`GET /mcp/tools`, `POST /mcp/invoke`, `GET /mcp/health`) | Two new servers (`tempo-mcp`, `ci-mcp`) on the same contract; a **capability router** in front of all clients |
| LangGraph graph (`supervisor → K8s∥Metrics∥Logs → RCA → Recommendation → finalize`) | New parallel branches (`tracing`, `deployment`); a **memory-retrieval** node before RCA |
| `LLMRouter` with `routing` / `analysis` / `summarization` roles | A new `embedding` role/provider for pgvector indexing + retrieval |
| Postgres checkpointer + bundled `pgvector/pgvector:pg16` image | pgvector is **already installed** — Phase 2 only adds the embeddings table + queries |
| Eval harness (22 golden scenarios, scorer, nightly `eval.yml`) | New trace/deploy/memory scenarios; a **timeline** eval; a memory **A/B** (with vs without retrieval) |
| AgentOps: `total_tokens_used` ledger + optional OTel/Phoenix | Retrieval + tool spans for the new agents; latency SLO tracking (TTFB) |
| Helm chart (api-gateway, mcp-k8s/prom/loki, postgres, redis, phoenix, web-ui) + 3 profiles | New `tempo-mcp` / `ci-mcp` deployments; Slack + CLI packaging; light multi-tenancy values |

**Release:** `v0.2.x`. **Action posture:** still **Observe** — plus **Notify**
(Slack). Zero cluster writes; write capability remains a Phase 4 concern.

---

## 1. Success Criteria

Phase 2 is **done** when all of these are true:

1. An investigation can pull **distributed traces** (Tempo) and **deployment /
   CI history** (Jenkins / GitHub Actions / ArgoCD) in addition to k8s + Prom +
   Loki, and the RCA correlates across all five signal types.
2. The system has **long-term memory**: when an investigation concludes it is
   embedded and stored; before the RCA reasons, similar past incidents are
   retrieved and injected as context. Retrieval measurably improves RCA on
   recurring patterns (see the memory A/B eval).
3. Every investigation produces a **timeline** (deploy → first anomaly → root
   cause → resolution) that is correct on ≥85% of timeline-eval scenarios.
4. A **Slack bot** answers `@kubepilot why is <service> failing?` in an incident
   channel and streams the result back inline.
5. A **CLI** (`kubepilot investigate <service>`) runs an investigation from a
   terminal / CI job with `--output json`.
6. The orchestrator is **MCP-adapter-agnostic**: an operator can point a
   capability (e.g. `query_metrics`) at the **official Grafana MCP server** or a
   community/vendor MCP without touching agent code (config-only).
7. **Light multi-tenancy**: investigations can be restricted to a per-user
   **namespace allowlist**, and the Web UI enforces `viewer` / `investigator`
   roles.
8. **Quality gates:** RCA accuracy **≥80%** on the golden dataset (up from 70%);
   median **time-to-first-byte < 5 s**; timeline correct ≥85%.
9. At least **one external user team** has reported a **real-incident win**.
10. Docs updated (Tempo/CI setup, memory, Slack, CLI, adapter config); `v0.2.0`
    tagged with a demo.

---

## 2. Scope

### 2.1 In Scope

| Item | Detail |
|---|---|
| Tracing Agent + `tempo-mcp` | `query_traces`, `get_trace`, `find_failed_spans`, `service_dependency_map` |
| Deployment Agent + `ci-mcp` | `get_deployment_history`, `get_recent_commits`, `get_pipeline_status`; pluggable Jenkins / GHA / ArgoCD backends |
| Long-term memory (pgvector) | Embed concluded incidents; hybrid retrieval (BM25 metadata + dense vector); inject top-K into the RCA prompt |
| Incident timeline generator | Deterministic timeline assembly from evidence timestamps + deploy events, LLM-labeled |
| Slack bot | Slack Bolt (Socket Mode default); slash command + app-mention; streamed result card |
| CLI | `typer`-based; `investigate`, `get`, `list`; `--output json/table`; talks to the gateway REST API |
| MCP adapter pattern | Capability → server routing table; reference config for Grafana LGTM MCP + a community k8s MCP |
| Light multi-tenancy | Namespace allowlist per API key/user; UI RBAC (`viewer`/`investigator`); audit of who ran what |
| Eval + AgentOps | Trace/deploy/memory scenarios; timeline eval; memory A/B; TTFB latency tracking |

### 2.2 Out of Scope (Explicitly — deferred to Phase 3+)

- Multi-agent **critique / debate** (Phase 3)
- **Knowledge graph** (services ↔ owners ↔ SLOs) (Phase 3)
- Runtime-specific RCA reasoning libraries (JVM/Node/Go internals) (Phase 3)
- Confidence **calibration**, prompt A/B/versioning, full guardrails (Phase 3)
- **Any cluster writes / remediation / HITL** (Phase 4 — the bright line)
- Full hard multi-tenant isolation (beyond namespace allowlists)
- Datadog/New Relic/ELK **first-party** adapters (the *pattern* ships; a specific
  vendor adapter as reference is a Phase 3 item — Phase 2 proves it with Grafana)
- Managed SaaS / multi-cluster federation

---

## 3. Repository Structure (Additions)

Phase 2 adds to the existing monorepo; nothing is restructured.

```text
services/
├── mcp-tempo/                 (NEW — Tempo MCP server, same REST contract)
├── mcp-ci/                    (NEW — CI/CD MCP server; Jenkins/GHA/ArgoCD backends)
├── slack-bot/                 (NEW — Slack Bolt app; calls the gateway API)
├── cli/                       (NEW — `kubepilot` typer CLI; installable console script)
└── orchestrator/
    └── src/kubepilot_orch/
        ├── agents/
        │   ├── tracing_agent.py       (NEW)
        │   ├── deployment_agent.py    (NEW)
        │   └── memory_agent.py        (NEW — retrieval node)
        ├── memory/                    (NEW — embeddings, store, hybrid retrieval)
        │   ├── embedder.py
        │   ├── store.py               (pgvector)
        │   └── retriever.py
        ├── timeline.py                (NEW — timeline assembly)
        ├── mcp/
        │   ├── client.py              (existing)
        │   └── adapter.py             (NEW — capability → server routing)
        └── prompts/
            ├── tracing_agent.md        (NEW)
            ├── deployment_agent.md     (NEW)
            └── rca_agent.md            (UPDATED — consumes memory_context)
charts/kubepilot-ai/templates/
├── mcp-tempo-*.yaml           (NEW)
├── mcp-ci-*.yaml              (NEW)
└── slack-bot-*.yaml           (NEW, optional/gated)
eval/datasets/
├── golden_rca_scenarios.jsonl (EXTENDED — +trace/deploy/memory scenarios)
└── golden_timeline_scenarios.jsonl (NEW)
docs/
├── tracing-and-ci.md          (NEW)
├── memory.md                  (NEW)
├── slack.md                   (NEW)
├── cli.md                     (NEW)
└── mcp-adapters.md            (NEW)
.github/workflows/
└── eval.yml                   (EXTENDED — memory A/B + timeline eval)
```

---

## 4. Milestones

Estimated for one full-time engineer; ~10 weeks + buffer. Compress with collaborators.

| Week | Milestone | Deliverable | Verification |
|---|---|---|---|
| **W1** | State v2 + MCP adapter scaffold | `memory_context` / `timeline` additive fields (schema v1→v2, migration entry only if a break is forced) + fixture; capability-router skeleton | Fixture-replay CI test passes for v1 **and** v2; adapter routes a capability to the existing servers |
| **W2** | `tempo-mcp` | Tempo MCP server + 4 trace tools, wired to a dev Tempo | Invoke `query_traces` / `find_failed_spans` from a client against dev Tempo |
| **W3** | Tracing Agent | LangGraph branch using `tempo-mcp`; latency-hotspot + failed-span evidence | Unit test: agent on a slow-dependency fixture surfaces the bottleneck span |
| **W4** | `ci-mcp` + Deployment Agent | CI MCP (Jenkins/GHA/ArgoCD backends) + agent correlating a recent deploy with the incident window | Test: "5xx spike 8 min after deploy v1.24.8" → deployment agent flags the deploy |
| **W5** | Memory store (pgvector) | `memory/` embed + store + hybrid retrieve; embeddings table; `embedding` LLM role | Integration test: conclude an incident → it is embedded; a similar query retrieves it top-1 |
| **W6** | Memory in the loop | `memory_agent` retrieval node before RCA; RCA prompt consumes `memory_context` | Memory A/B eval: retrieval improves score on the recurring-incident subset |
| **W7** | Timeline generator | `timeline.py` assembles + LLM-labels the chronology; surfaced in state + API + UI | Timeline eval ≥85% on `golden_timeline_scenarios.jsonl` |
| **W8** | Slack bot | Bolt app: app-mention + slash command; streamed result card; namespace-scoped | Manual: `@kubepilot why is X failing?` in a test workspace returns a card |
| **W9** | CLI + MCP adapter (Grafana) | `kubepilot` CLI (`investigate/get/list`, `--output json`); adapter config that swaps `mcp-prom`+`mcp-loki`+`mcp-tempo` for the official Grafana MCP | `kubepilot investigate payment-service` works; a Grafana-MCP profile runs an investigation |
| **W10** | Multi-tenancy + latency | Namespace allowlists per key; UI `viewer`/`investigator` roles; TTFB instrumentation + <5 s target | Authz test: a key scoped to `ns-a` is denied `ns-b`; TTFB dashboard shows <5 s median |
| **W11** | Eval + docs + release | Accuracy ≥80%; all new docs; `v0.2.0` tagged + demo | Nightly eval ≥80%; external tester completes a Slack + CLI flow |

**Buffer week (W12)** — integration bugs surfaced during W10–W11. Do not skip.

---

## 5. Component Deliverables (Detail)

### 5.1 `tempo-mcp` — Tempo MCP Server

**Tools (read-only):**
- `query_traces(service, start, end, tags?, limit?)`
- `get_trace(trace_id)` — spans, timings, status
- `find_failed_spans(service, window)` — error/abnormal spans
- `service_dependency_map(service, window)` — upstream/downstream edges

**Contract:** same REST surface as the Phase 1 MCP servers (`/mcp/tools`,
`/mcp/invoke`, `/mcp/health`). Bearer token to Tempo via k8s Secret.
**Response shapes are curated** (like `PodSummary`): return a `TraceSummary`
(root duration, error count, slowest span, dependency edges) rather than raw
spans — the same token-efficiency argument as ARCHITECTURE §3.3.1.

**Acceptance:** a fixture trace with a slow downstream call yields a
`TraceSummary` whose `slowest_span.service` is the downstream dependency.

### 5.2 Tracing Agent

A specialist node (parallel with K8s/Metrics/Logs) that uses `tempo-mcp` to find
latency hotspots and failed spans, emitting `Evidence(kind="latency_hotspot" |
"failed_span" | "dependency_edge")`. Thin shell over the existing
`agents/_runner.py` tool-loop — no new orchestration machinery.

### 5.3 `ci-mcp` — Deployment / CI MCP Server

**Tools:**
- `get_deployment_history(service, window)`
- `get_recent_commits(service|repo, window)`
- `get_pipeline_status(service|repo)`

**Pluggable backend** selected by config: `jenkins` (API token), `github_actions`
(PAT), `argocd` (token). One tool surface, three adapters behind it — the agent
is backend-agnostic.

**Acceptance:** given a deploy event 8 minutes before the incident window, the
Deployment Agent emits `Evidence(kind="recent_deploy", severity=warning)` with
the version + timestamp, and the RCA can cite it.

### 5.4 Long-Term Memory (pgvector)

**Store (`memory/store.py`):** an `incident_embeddings` table in the bundled
Postgres (pgvector already installed via the `pgvector/pgvector:pg16` image).
Columns: `incident_id`, `summary`, `embedding vector(N)`, `root_cause_category`,
`namespace`, `service`, `outcome`, `created_at`.

**Embedder (`memory/embedder.py`):** a new **`embedding`** LLM role in the router
(cloud embeddings or a local model for air-gapped). Concluded investigations are
embedded on finalize.

**Retriever (`memory/retriever.py`):** **hybrid** — BM25/`tsvector` on metadata +
dense cosine similarity on the summary, re-ranked, top-K.

**In the loop:** a `memory_agent` node runs after the specialists and before RCA,
populating `state.memory_context: list[PastIncident]`. The RCA prompt gains a
"Similar past incidents" section.

**Acceptance / A/B:** on a held-out set of recurring incidents, RCA score with
retrieval on is measurably higher than with it off (report the delta).

### 5.5 Incident Timeline Generator (`timeline.py`)

Assembles a chronology from evidence `collected_at` + deploy events + first-alert
times into an ordered `list[TimelineEntry]`, then an LLM pass labels each entry
(`deploy_started`, `first_anomaly`, `oomkilled`, `alert_fired`, …). Deterministic
ordering; LLM only labels. Surfaced in `state.timeline`, the API response, and the
Web UI investigation view.

**Acceptance:** timeline eval — for each scenario, assert the emitted ordering and
key labels match the expected chronology; ≥85% correct.

### 5.6 Slack Bot (`services/slack-bot`)

Slack Bolt app (Socket Mode by default — no public ingress required). Handles
`@kubepilot …` app-mentions and a `/kubepilot` slash command; POSTs to the
gateway, streams progress, and posts a result card (root cause, confidence,
top recommendations, link to the full report). Honors the caller's namespace
allowlist. Deployed as a gated Helm subcomponent.

### 5.7 CLI (`services/cli`)

`typer` app, installed as a `kubepilot` console script.
```text
kubepilot investigate <service> --namespace prod [--output json|table] [--wait]
kubepilot get <incident_id> [--output json]
kubepilot list [--limit N]
```
Config via `~/.kubepilot/config.toml` or env (`KUBEPILOT_API_URL`,
`KUBEPILOT_API_KEY`). `--output json` for CI pipelines.

### 5.8 MCP Adapter Pattern (`mcp/adapter.py`)

A capability registry maps a **capability** (`find_pod`, `query_metrics`,
`query_logs`, `query_traces`, `search_exceptions`, …) to the MCP server that
provides it. Agents request capabilities; the adapter resolves to a server.
Default config maps to KubePilot's own servers (the reference implementation);
an alternate profile maps `query_metrics`/`query_logs`/`query_traces` to the
**official Grafana MCP server** (one server, three signals). Config-only swap,
no agent code changes. Documented in `docs/mcp-adapters.md` and
ARCHITECTURE §3.3.1.

### 5.9 Light Multi-Tenancy

- **Namespace allowlist** per API key (extend the gateway auth: a key maps to a
  set of allowed namespaces; investigations outside it are `403`).
- **UI roles** `viewer` (read reports) / `investigator` (trigger + read).
- **Audit**: who triggered what, when, against which namespace — written to the
  existing audit path and exportable via OTel.

---

## 6. Architecture Changes

- **State schema v1 → v2 (additive):** `memory_context: list[PastIncident] = []`,
  `timeline: list[TimelineEntry] = []`, and trace/deploy evidence kinds. Per
  ARCHITECTURE §3.2.1 this is additive → **no migration function needed**, but a
  **v2 checkpoint fixture** is added and the fixture-replay test must load both
  v1 and v2. Bump `CURRENT_SCHEMA_VERSION` to 2.
- **Graph:** two new parallel specialist branches (`tracing`, `deployment`) fan
  in alongside the existing three; a serial `memory` node sits between the
  fan-in and `rca`. Reducer-merged fields keep parallel updates safe (unchanged
  discipline).
- **Persistence:** new `incident_embeddings` table; embeddings written on
  finalize; retrieval read before RCA.
- **Helm:** `mcp-tempo` + `mcp-ci` deployments (same security context /
  read-only posture as the Phase 1 MCP servers); optional `slack-bot`; new
  values blocks for adapters + tenancy. pgvector needs no chart change.
- **RCA prompt** updated to weigh retrieved memory and cite it in reasoning.

---

## 7. Eval Strategy (Phase 2 Updates)

- **Extend the golden set** with trace-driven, deploy-driven, and multi-signal
  scenarios (e.g. "latency spike from a slow dependency", "5xx after deploy").
- **New timeline eval** (`golden_timeline_scenarios.jsonl`): score ordering +
  key-label correctness; gate ≥85%.
- **Memory A/B:** run the recurring-incident subset twice (retrieval on/off);
  report the accuracy delta; retrieval must not regress non-recurring scenarios.
- **Latency eval:** measure TTFB (trigger → first agent output) in the harness;
  gate median < 5 s (with mocked tool latency held constant).
- **Baseline gate raised to ≥80%** overall (`run_eval.py` threshold bumped). The
  deterministic self-test still runs in PR CI; the live nightly runs the full set
  plus the A/B.

---

## 8. Testing Strategy

| Layer | Phase 2 additions |
|---|---|
| Unit | Tracing/Deployment/Memory agents (LLM mocked); retriever ranking; timeline assembly; adapter resolution |
| Contract | `tempo-mcp` / `ci-mcp` tool schemas + read-only posture (mirror `test_rbac.py` intent for CI backends: read-only tokens) |
| Integration | Memory round-trip against real Postgres+pgvector (testcontainers); adapter against a live Grafana MCP in a kind profile |
| End-to-end | Full 5-signal investigation in kind (Tempo + a CI stub) producing an RCA + timeline; Slack + CLI happy paths |
| Eval | Golden (≥80%), timeline (≥85%), memory A/B, TTFB |

**Coverage target:** maintain ≥70% line coverage on orchestrator + MCP servers,
including the new modules.

---

## 9. Demo Acceptance Criteria (v0.2.0)

The demo video must show:
1. `helm install` (prod-small profile) with `tempo-mcp` + `ci-mcp` enabled.
2. Inject an incident whose root cause is a **recent deploy** causing a
   **downstream latency** failure.
3. Trigger via **Slack**: `@kubepilot why is checkout-service slow?`
4. Watch agents stream: K8s → Metrics → Logs → **Tracing** → **Deployment** →
   **Memory (retrieves a similar past incident)** → RCA → Recommendation.
5. Result card: root cause cites the deploy **and** the slow dependency span;
   confidence ≥0.85; **timeline** rendered; a "similar past incident" reference.
6. Re-run the **same** incident and show memory making the second RCA faster /
   more confident.
7. Run the **CLI**: `kubepilot investigate checkout-service --output json`.
8. Swap to the **Grafana MCP** profile (config-only) and run one investigation.
9. Phoenix: traces for the new agents + the memory-retrieval span; token ledger.

If any step needs manual intervention or fails first try, Phase 2 isn't done.

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Memory adds latency, blows the <5 s TTFB target | Med | Med | Retrieve in parallel with specialists; cap K; index tuning; async embed-on-finalize (off the hot path) |
| Embedding model choice hurts air-gapped installs | Med | Med | Support a local embedding model; document min model; make memory **opt-out** via values |
| Memory retrieves irrelevant incidents → worse RCA | Med | High | Hybrid retrieval + re-rank + a relevance floor; the A/B eval **gates** memory; retrieval must not regress non-recurring scenarios |
| CI backends (Jenkins/GHA/ArgoCD) vary wildly | High | Med | One capability surface, thin per-backend adapters; ship GHA first (most common), others behind config |
| Tempo/trace data sparse in many clusters | High | Low | Tracing agent degrades gracefully (no traces → no trace evidence, not an error), exactly like the Phase 1 tool-error-as-evidence pattern |
| Slack app-review / Socket Mode friction | Med | Low | Socket Mode default (no public URL, no app review for internal use); document manifest |
| Adapter pattern leaks server-specific quirks into agents | Med | Med | Capabilities defined by **our** curated response shapes; adapters must map to them, not the reverse |
| Scope creep from Phase 3 (knowledge graph, debate) | High | High | Locked in §2.2; defer to issues; the P3/P4 bright lines are non-negotiable |
| State v2 breaks in-flight v1 checkpoints | Low | Med | Additive-only + v1 **and** v2 fixture-replay test in the same PR (§3.2.1) |

---

## 11. Definition of Done (v0.2.0 Release Checklist)

- [ ] Tracing Agent + `tempo-mcp` shipped, unit + integration tested
- [ ] Deployment Agent + `ci-mcp` (GHA + at least one of Jenkins/ArgoCD) shipped
- [ ] Long-term memory: embed-on-finalize + hybrid retrieval + RCA injection
- [ ] Memory A/B eval shows retrieval improves recurring-incident accuracy, no regression elsewhere
- [ ] Incident timeline generator; timeline eval ≥85%
- [ ] Slack bot completes an investigation end-to-end (namespace-scoped)
- [ ] CLI (`investigate`/`get`/`list`, `--output json`) shipped + installable
- [ ] MCP adapter pattern + a working **Grafana MCP** profile (config-only swap)
- [ ] Light multi-tenancy: namespace allowlists + UI `viewer`/`investigator` + audit
- [ ] State schema v2 (additive) with v1 **and** v2 fixture-replay tests green
- [ ] RCA accuracy **≥80%**; median TTFB **< 5 s**
- [ ] Helm chart installs cleanly (dev / prod-small / prod-air-gapped) with the new components
- [ ] AgentOps: spans for the new agents + memory retrieval; TTFB tracked
- [ ] CI green: lint, unit, integration, eval-subset on every PR; nightly full eval + A/B
- [ ] Docs: tracing-and-ci, memory, slack, cli, mcp-adapters + updated install/architecture
- [ ] **≥1 external user team reports a real-incident win**
- [ ] GitHub release `v0.2.0` with changelog + demo video

---

## 12. After Phase 2

When every box above is green, move to [ROADMAP.md](roadmap.md) Phase 3
(multi-agent critique, knowledge graph, advanced RCA, eval/calibration,
guardrails). **Do not start Phase 3 before Phase 2 ships.** The bright line into
Phase 4 (writes to the cluster) stays untouched until Phase 3 quality is proven.
Scope discipline remains the single biggest determinant of success.
