# KubePilot AI — Phase 3 Implementation Plan

> **Goal:** Make KubePilot trustworthy enough for enterprise production. Phase 1
> proved the loop works; Phase 2 made it a daily tool. **Phase 3 is where
> quality, evaluation, and explainability dominate** — multi-agent critique, a
> cluster knowledge graph, runtime-specific RCA, continuous eval with drift
> detection + confidence calibration, prompt versioning, guardrails, RBAC v2, and
> a pluggable observability-adapter interface (Datadog as the reference).

> Reference: [architecture.md](./architecture.md) (the *what*), this doc (the
> *how* for v0.3.x), [roadmap.md](./roadmap.md) (the *when*),
> [phase-2-plan.md](./phase-2-plan.md) (what shipped in v0.2.x and is assumed
> here). **Do not start Phase 3 until Phase 2 is tagged and demoed.**

---

## 0. What Phase 1 + 2 Give Us (Starting Point)

Phase 3 extends real seams. The pieces below already exist and are the extension
points for this phase:

| Existing artifact | Phase 3 extends it by… |
|---|---|
| LangGraph graph (conditional fan-out → memory → RCA → recommendation → finalize) | A **critic** node between RCA and finalize; a **knowledge** retrieval node beside memory |
| `InvestigationState` (Pydantic, additive schema-versioning discipline, currently v2) | **Additive** v3 fields: `critique`, `knowledge_context`, `calibrated_confidence` (+ fixture-replay) |
| RCA agent + `prompts/rca_agent.md`; caller-owned structured-output validation | Runtime-specific reasoning libraries injected into the RCA prompt; guardrails wrapping its output |
| LLM router with `routing`/`analysis`/`summarization` roles | A `critique` role; **prompt versioning** so each prompt has a promotable/rollback-able version |
| Long-term memory (pgvector, `memory/`) — retrieve-before-RCA, embed-on-finalize | A **cluster knowledge graph** (relational + pgvector) reusing the same Postgres + embedder seam |
| MCP **capability router** (`mcp/adapter.py`) — domain → server, config-only swap | A typed **observability-adapter interface** (MetricsProvider / LogsProvider) with a **Datadog** reference |
| Eval harness (`eval/`) — 26 golden + timeline + memory-A/B, deterministic self-test + live gate | **Calibration**, **drift detection**, **prompt A/B**, **debate-uplift**; release-blocking regression gate |
| Light multi-tenancy (`auth.py` `Principal{role, namespaces}`, viewer/investigator) | **RBAC v2**: operator/admin roles, namespace-scoped tokens, OIDC (optional), SIEM audit export |
| AgentOps (OTel + token ledger) | Calibration plot + eval/drift dashboard; guardrail + critique traces |

**Release:** `v0.3.x`. **Action posture:** still **Observe** — plus **Reason**
(critique, knowledge, calibrated confidence). Zero cluster writes; the bright
line into Phase 4 (writes) stays untouched until Phase 3 quality is proven.

---

## 1. Success Criteria

Phase 3 is **done** when all of these are true:

1. **Multi-agent critique** runs after RCA and demonstrably improves quality — on
   a held-out set, critiqued RCAs beat single-pass RCAs (accuracy and/or
   calibration), and low-agreement cases are flagged for human review.
2. A **cluster knowledge graph** (services ↔ owners ↔ dependencies ↔ SLOs ↔
   historical incidents) is populated and queried: the Kubernetes agent knows
   "payment-service is owned by team X, depends on Y, last deployed by Z," and
   the RCA weighs it.
3. **Runtime-specific RCA** libraries (JVM, Node.js, Python, Go) sharpen
   diagnoses for those runtimes without hardcoding per-language logic in code
   paths — the intelligence lives in a knowledge library keyed off the Logs
   agent's `detail.runtime`.
4. **RCA accuracy ≥90%** on the golden dataset.
5. **Confidence calibration error <10%** — a stated "85% confident" is correct
   ~85% of the time — with a calibration plot in AgentOps.
6. **Continuous eval + drift detection**: golden + held-out evals run
   continuously; a drift alert fires when accuracy or calibration degrades; the
   release pipeline **auto-blocks** on an accuracy regression >5%.
7. **Prompt versioning + A/B**: every prompt is versioned; a change is A/B-tested
   against golden + held-out before promotion; a regression can be **rolled back
   in <5 minutes**.
8. **Guardrails** on RCA output: forbidden-recommendation checks, schema
   enforcement, and prompt-injection defense (tool outputs are sanitized before
   being fed back to the model).
9. **RBAC v2**: viewer / investigator / operator / admin roles, namespace-scoped
   tokens, and audit-log export to a SIEM (OTel). OIDC/Keycloak optional.
10. **Observability adapter** interface shipped with a **Datadog** reference
    implementation an external Datadog user can install and run.
11. **≥3 external user teams** in production usage; `v0.3.0` tagged + demoed.

---

## 2. Scope

### 2.1 In Scope

| Item | Detail |
|---|---|
| Multi-agent critique / debate | `critic-agent` node; per-finding refutation; agreement scoring; escalation flag for high-stakes / low-agreement incidents |
| Cluster knowledge graph | services / owners / deps / SLOs / incident history in Postgres (+ pgvector); ingestion (labels, ownership file, ServiceMonitors, dep discovery) |
| Advanced RCA runtime libraries | JVM (GC/heap/thread dumps), Node (event loop / async leaks), Python (GIL / gunicorn), Go (goroutine leaks / GC) knowledge, injected by runtime |
| Evaluation framework | Continuous golden + held-out eval; DeepEval custom metrics; LangSmith (optional); drift detection; release-gate on regression |
| Confidence calibration | Map raw model confidence → empirical accuracy from eval history; calibrated_confidence surfaced + plotted |
| Prompt versioning + A/B | Versioned prompt registry; eval-gated promotion; instant rollback |
| Guardrails | Forbidden-recommendation policy, output-schema enforcement, prompt-injection sanitization of tool results |
| RBAC v2 | operator/admin roles, namespace-scoped tokens, OIDC (optional), SIEM audit export |
| Observability adapters | MetricsProvider / LogsProvider interface + Datadog reference adapter |

### 2.2 Out of Scope (deferred to Phase 4)

- **Any cluster writes / remediation / HITL approval / auto-rollback / self-heal**
  — the whole write subsystem is Phase 4. Phase 3 must not cross that line.
- Blast-radius estimation, execution policy engine, `k8s-write-mcp`.
- Multi-cluster federation, managed SaaS, predictive/proactive SRE.
- Full hard multi-tenant isolation beyond RBAC v2 + namespace scoping.

---

## 3. Repository Structure (Additions)

```text
services/orchestrator/src/kubepilot_orch/
├── agents/
│   └── critic_agent.py            (NEW — refutes/scores the RCA before finalize)
├── knowledge/                     (NEW — cluster knowledge graph)
│   ├── graph.py                   (services/owners/deps/SLOs schema + queries)
│   ├── ingest.py                  (populate from labels/ownership/ServiceMonitors)
│   └── retriever.py               (knowledge_context for the RCA/K8s agents)
├── rca/
│   └── runtimes/                  (NEW — runtime-specific reasoning libraries)
│       ├── jvm.md  node.md  python.md  go.md
│       └── library.py             (select by Logs agent detail.runtime)
├── guardrails/                    (NEW — output + prompt-injection defense)
│   ├── policy.py                  (forbidden recommendations, schema checks)
│   └── sanitize.py                (scrub tool results before re-feeding the model)
├── prompts/
│   ├── critic_agent.md            (NEW)
│   └── registry.py                (NEW — versioned prompt loader + A/B selector)
└── calibration/                   (NEW — confidence calibration)
    └── calibrator.py

services/
├── mcp-datadog/                   (NEW — reference observability adapter, same REST contract)
└── api-gateway/…/auth.py          (RBAC v2: operator/admin, OIDC, audit export)

eval/harness/
├── calibration.py                 (NEW — reliability curve, calibration error)
├── drift.py                       (NEW — compare vs baseline, alert)
├── prompt_ab.py                   (NEW — A/B a prompt version vs current)
└── debate_eval.py                 (NEW — critique-uplift on a held-out set)

docs/features/  knowledge-graph.md · guardrails.md · rbac.md · observability-adapters.md
.github/workflows/  eval-gate.yml   (NEW — release-blocking accuracy regression check)
```

---

## 4. Milestones

~12 weeks + buffer for one full-time engineer. Compress with collaborators.

| Week | Milestone | Deliverable | Verification |
|---|---|---|---|
| **W1** | State v3 + prompt registry | `critique`/`knowledge_context`/`calibrated_confidence` additive fields (v2→v3 + fixture); versioned prompt loader | Fixture-replay loads v1/v2/v3; a prompt resolves by version |
| **W2** | Critic agent | `critic_agent` + node between RCA and finalize; agreement score + escalation flag | Unit: critic lowers confidence on a contradictory RCA fixture |
| **W3** | Debate eval | `debate_eval.py` on a held-out set; report critique uplift | Critiqued RCA ≥ single-pass on the held-out set |
| **W4** | Knowledge graph store + ingest | `knowledge/` schema + ingestion from labels/ownership | Integration: ingest a fixture cluster; query owner/deps of a service |
| **W5** | Knowledge in the loop | knowledge node + K8s/RCA prompts consume ownership/deps/SLO | Test: RCA cites the owning team + a known dependency |
| **W6** | Runtime RCA libraries | JVM/Node/Python/Go knowledge injected by `detail.runtime` | Per-runtime eval scenarios improve category accuracy |
| **W7** | Confidence calibration | `calibrator.py` maps raw→empirical from eval history; `calibrated_confidence` + plot | Calibration error <10% on the eval set |
| **W8** | Continuous eval + drift + gate | `drift.py` + `eval-gate.yml` blocking >5% regression; DeepEval metrics | A seeded regression blocks the gate; drift alert fires |
| **W9** | Prompt A/B + rollback | `prompt_ab.py`; promote only if it beats current on golden+held-out; one-command rollback | A worse prompt is rejected; rollback in <5 min |
| **W10** | Guardrails | forbidden-rec policy + schema enforcement + tool-output sanitization | Injection fixture is neutralized; a forbidden rec is blocked |
| **W11** | RBAC v2 + observability adapter | operator/admin + namespace-scoped tokens + SIEM export; MetricsProvider/LogsProvider interface + `mcp-datadog` | Authz matrix test; an investigation runs entirely via the Datadog adapter |
| **W12** | Accuracy ≥90% + docs + release | Hit accuracy/calibration; all docs; `v0.3.0` tagged + demo | Nightly eval ≥90%, calibration <10%; external Datadog user runs it |

**Buffer week (W13).** Don't skip.

---

## 5. Component Deliverables (Detail)

### 5.1 Multi-Agent Critique (`agents/critic_agent.py`)

A node that runs **after** RCA and **before** recommendation/finalize. It receives
the `RCAReport` + the evidence and is prompted to **refute** — find missing
signals, alternative causes, and unsupported leaps. It emits a `Critique`
{agreement: float, concerns: list, adjusted_confidence: float}. The graph uses it
to (a) adjust `calibrated_confidence`, (b) set an `escalate_to_human` flag when
agreement is low, and (c) feed concerns into the recommendation. For high-stakes
incidents, run **N** critics with distinct lenses (correctness / alternative-cause
/ evidence-sufficiency) and require a majority — the perspective-diverse verify
pattern. Deterministic tests use scripted critics; the uplift is measured live.

**Acceptance:** on a held-out set, critiqued RCAs beat single-pass on accuracy or
calibration; a contradictory-evidence fixture yields low agreement + escalation.

### 5.2 Cluster Knowledge Graph (`knowledge/`)

Postgres tables (+ pgvector for semantic lookups) for **services, owners,
dependencies, SLOs, and incident history**, reusing the Phase 2 Postgres +
embedder seam. `ingest.py` populates from pod/namespace **labels**, an
**ownership file** (`kubepilot.io/owner` annotations or a values-provided map),
**ServiceMonitors/PrometheusRules** (SLOs), and dependency discovery (from the
Tracing agent's `service_dependency_map` + NetworkPolicies). A knowledge node
populates `state.knowledge_context`; the K8s and RCA prompts weigh ownership +
dependencies + SLO breach.

**Acceptance:** given a fixture cluster, the RCA references the owning team and a
known dependency; a dependency's failure is correlated to the target service.

### 5.3 Advanced RCA Runtime Libraries (`rca/runtimes/`)

Per-runtime knowledge (JVM: GC/heap/thread-dump patterns; Node: event-loop
stalls / async leaks; Python: GIL contention / gunicorn worker timeouts; Go:
goroutine leaks / GC pressure) as versioned markdown. `library.py` selects the
relevant snippet from the Logs agent's `detail.runtime` and injects it into the
RCA prompt. **No per-language branching in code** — the intelligence is data.

**Acceptance:** runtime-tagged golden scenarios (a Java OOM vs a Go goroutine
leak) get more specific, correct root-cause categories with the library on.

### 5.4 Evaluation Framework + Drift + Release Gate (`eval/`)

Extend the harness: **continuous** golden + **held-out** runs; **DeepEval**
custom metrics (faithfulness to evidence, category correctness); optional
**LangSmith** datasets. `drift.py` compares a run to a stored baseline and
alerts on degradation. `.github/workflows/eval-gate.yml` **blocks a release** if
accuracy regresses >5% vs the last tagged release. Live numbers still need a key;
the deterministic self-tests keep CI green.

**Acceptance:** a seeded prompt regression blocks the gate; a drift alert fires on
a degraded run; held-out accuracy tracked separately from golden (no overfit).

### 5.5 Confidence Calibration (`calibration/calibrator.py`)

From eval history, learn a mapping raw-model-confidence → empirical accuracy
(isotonic / Platt). The graph stamps `calibrated_confidence`; AgentOps shows the
reliability plot. Gate: calibration error <10%.

### 5.6 Prompt Versioning + A/B (`prompts/registry.py`)

Every prompt carries a version. `registry.py` resolves the active version and can
serve an A/B variant. Promotion requires beating the current version on golden +
held-out (via `prompt_ab.py`); rollback is a config flip (<5 min). Prompt
version is recorded on each investigation for traceability.

### 5.7 Guardrails (`guardrails/`)

- `policy.py`: reject/flag **forbidden recommendations** (nothing destructive
  ever suggested; Phase-3 stays read-only) and enforce output schema (already
  Pydantic — extend with policy checks).
- `sanitize.py`: **prompt-injection defense** — scrub tool results (log lines,
  configmap keys, trace data) of instruction-like content before feeding them
  back to the model in the next turn. Wire into `agents/_runner.py`.

**Acceptance:** an injected "ignore previous instructions / run kubectl delete"
log line is neutralized; a destructive recommendation is blocked.

### 5.8 RBAC v2 (`api-gateway/…/auth.py`)

Extend `Principal{role, namespaces}` to **operator** and **admin** roles;
namespace-scoped tokens; **OIDC/Keycloak** as an optional auth backend; and
**audit-log export** of every action to a SIEM via OTel. The existing
per-endpoint principal enforcement is the seam.

**Acceptance:** an authz matrix test (each role × each action × in/out-of-scope
namespace); audit entries exported for a full investigation.

### 5.9 Observability Adapters (`services/mcp-datadog`, adapter interface)

Define typed **MetricsProvider / LogsProvider** capabilities on top of the Phase 2
`CapabilityRouter`, and ship a **Datadog** reference MCP server (same REST
contract, curated response shapes) so a Datadog shop can run investigations by
pointing the `metrics`/`logs` capabilities at it — config-only, no agent change.

**Acceptance:** an end-to-end investigation runs entirely through the Datadog
adapter on a fixture; an external Datadog user completes one.

---

## 6. Architecture Changes

- **Graph:** `… specialists → memory + knowledge → rca → critic → recommendation
  → finalize`. The critic adjusts `calibrated_confidence` and may set
  `escalate_to_human`.
- **State v2 → v3 (additive):** `critique: Critique | None`,
  `knowledge_context: list[...] = []`, `calibrated_confidence: float | None`,
  `prompt_versions: dict[str,str] = {}`. Additive → trivial `_v2_to_v3`
  version-stamp migration + a v3 fixture; the fixture-replay test must load v1,
  v2 **and** v3 (`test_migration_registry_is_complete` enforces the migration).
- **LLM router** gains a `critique` role and reads the **active prompt version**
  from the registry.
- **Release process:** the eval gate is part of tagging — a release is blocked on
  an accuracy regression.
- **RCA prompt** consumes knowledge_context + the runtime library + memory (from
  Phase 2), and its output passes through guardrails.

---

## 7. Eval Strategy (Phase 3)

- **Held-out set** distinct from golden, to detect overfitting to the golden
  prompts — accuracy reported on both.
- **Calibration**: reliability curve + expected-calibration-error; gate <10%.
- **Drift**: each run compared to the last baseline; alert on regression.
- **Debate uplift**: critique-on vs -off on the held-out set; critique must not
  regress non-ambiguous cases.
- **Prompt A/B**: a new version promotes only if it beats current on golden +
  held-out.
- **Baseline raised to ≥90%** overall. Deterministic self-tests stay in PR CI;
  the live gate (`eval-gate.yml`) blocks regressive releases.

---

## 8. Testing Strategy

| Layer | Phase 3 additions |
|---|---|
| Unit | Critic scoring; knowledge queries; runtime-library selection; calibrator math; guardrail policy + sanitizer; prompt registry resolution |
| Contract | `mcp-datadog` tool schemas + read-only posture; observability-provider interface conformance |
| Integration | Knowledge ingest against real Postgres+pgvector; Datadog adapter (mocked API) end-to-end; OIDC flow |
| End-to-end | Full investigation with critique + knowledge + guardrails in kind; RBAC authz matrix |
| Eval | Golden ≥90%, held-out, calibration <10%, drift, debate uplift, prompt A/B |

Keep ≥70% line coverage on orchestrator + MCP servers incl. the new modules.
Remember: `ScriptedLLM` bypasses provider conversion — validate critique/guardrail
behavior with `live_llm`/integration tests, not only scripted ones.

---

## 9. Demo Acceptance Criteria (v0.3.0)

1. Investigation of a JVM OOM: RCA cites the **runtime-specific** pattern, the
   **owning team** (knowledge graph), a **known dependency**, and a **calibrated**
   confidence.
2. The **critic** disagrees on a deliberately ambiguous incident → low agreement →
   **escalate-to-human** flag shown in the UI.
3. AgentOps: the **calibration plot** and the **eval/drift dashboard**.
4. A **prompt A/B**: promote a better prompt; then **roll back** in <5 minutes.
5. A **prompt-injection** log line is neutralized (guardrails) — shown live.
6. Run the **same** investigation via the **Datadog adapter** (config-only swap).
7. **RBAC**: an operator can see everything; a namespace-scoped investigator is
   denied another namespace; the action is in the exported audit log.

If any step needs manual intervention or fails first try, Phase 3 isn't done.

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Critic adds latency/cost without quality gain | Med | Med | Gate critique on the debate-uplift eval; run multi-critic only for high-stakes/low-agreement cases |
| Knowledge graph goes stale / wrong | High | Med | Ingest continuously; treat knowledge as corroborating context (never overrides current signals); show freshness |
| Calibration overfits the golden set | Med | High | Calibrate + measure on a **held-out** set; recalibrate on drift |
| Prompt A/B noise (LLM non-determinism) | High | Med | temperature=0, multi-run averaging, require a margin beyond noise before promoting |
| Guardrail sanitization strips real signal | Med | Med | Conservative injection heuristics + an allowlist; measure recall impact in eval |
| Datadog adapter drifts from our curated shapes | Med | Med | The adapter must map to **our** capability response models, not the reverse; contract tests |
| Reasoning creep toward Phase-4 writes | High | High | Guardrails forbid destructive recommendations; the read/write bright line is enforced in code + reviewed |
| Enterprise auth (OIDC) integration variance | Med | Med | Keep the static-token path; OIDC is opt-in and adapter-shaped |

---

## 11. Definition of Done (v0.3.0 Release Checklist)

- [ ] Critic node shipped; debate-uplift eval shows critiqued ≥ single-pass on held-out
- [ ] Cluster knowledge graph populated + queried; RCA cites owner/deps/SLO
- [ ] Runtime RCA libraries (JVM/Node/Python/Go) improve per-runtime accuracy
- [ ] RCA accuracy **≥90%** (golden) + tracked on a held-out set
- [ ] Confidence calibration error **<10%**; calibration plot in AgentOps
- [ ] Continuous eval + drift detection; release gate blocks >5% regression
- [ ] Prompt versioning + A/B; rollback demonstrated in <5 min
- [ ] Guardrails: forbidden-rec + schema + prompt-injection defense, tested
- [ ] RBAC v2: viewer/investigator/operator/admin + namespace-scoped tokens + SIEM export (OIDC optional)
- [ ] Observability-adapter interface + Datadog reference; an external Datadog user runs it
- [ ] State schema v3 (additive) with v1/v2/v3 fixture-replay green
- [ ] Docs: knowledge-graph, guardrails, rbac, observability-adapters + updated architecture
- [ ] CI green (lint, unit, integration, eval-subset) + nightly full eval; eval-gate wired
- [ ] **≥3 external user teams** in production usage
- [ ] GitHub release `v0.3.0` with changelog + demo video

---

## 12. After Phase 3

When every box above is green, move to [roadmap.md](./roadmap.md) Phase 4 —
**the bright line: writes to the cluster**, HITL approval, execution policies,
auto-rollback, self-healing. Do not cross it until Phase 3's accuracy,
calibration, drift detection, and guardrails are proven in production. The whole
point of Phase 3 is to earn the trust that Phase 4 spends.
