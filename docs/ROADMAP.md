# KubePilot AI — Roadmap

> All four phases of the project, with concrete deliverables, success criteria, and the boundary between phases.

> Source-of-truth pairing: [IDEA.md](../IDEA.md) (product vision), [ARCHITECTURE.md](./ARCHITECTURE.md) (engineering view), [PHASE_1_PLAN.md](./PHASE_1_PLAN.md) (next-up execution).

---

## Roadmap At a Glance

| Phase | Theme | Duration target | Deliverable | Action posture |
|---|---|---|---|---|
| **Phase 1** | MVP — Read-Only Investigator | ~12 weeks | Autonomous read-only RCA for any k8s workload | **Observe** |
| **Phase 2** 🚧 | Production-Ready Analysis | ~10 weeks | MCP-native, persistent memory, timeline, Slack, CLI | **Observe + Notify** |
| **Phase 3** | Enterprise-Grade Agentic SRE | ~12 weeks | Multi-agent collaboration, eval framework, knowledge graph, advanced RCA | **Observe + Reason** |
| **Phase 4** | Autonomous Operations | ~12 weeks | HITL-gated remediation, self-healing, rollback automation | **Observe + Act (with approval)** |

Each phase is a **shippable release**. No phase begins until the previous one has a tagged, documented, demoed release.

---

# Phase 1 — MVP: Read-Only Incident Investigator

**Release:** `v0.1.x`
**Detail:** Full implementation plan in [PHASE_1_PLAN.md](./PHASE_1_PLAN.md).

## Goal

Demonstrate that a multi-agent system can autonomously investigate Kubernetes incidents across **any workload** (Java, Python, Node, Go, etc.) and produce a credible root-cause analysis with evidence — with zero write access to the cluster.

## Scope

| Component | Status in Phase 1 |
|---|---|
| Supervisor Agent | ✅ |
| Kubernetes Agent | ✅ |
| Metrics Agent (Prometheus) | ✅ |
| Logs Agent (Loki) | ✅ |
| RCA Agent | ✅ |
| Recommendation Agent | ✅ (suggestions only, no execution) |
| Tracing Agent | ❌ → Phase 2 |
| Deployment Agent | ❌ → Phase 2 |
| Remediation Agent | ❌ → Phase 4 |
| MCP servers: k8s, prom, loki | ✅ |
| Web UI (basic) | ✅ |
| Helm chart (dev / prod-small / prod-air-gapped) | ✅ |
| BYOK LLM (Anthropic / OpenAI / Bedrock / Azure / Ollama / vLLM) | ✅ |
| AgentOps (Phoenix self-hosted, LangSmith optional) | ✅ |
| Eval framework (20+ golden scenarios) | ✅ |
| Long-term memory (pgvector) | ❌ → Phase 2 |
| Slack bot, CLI | ❌ → Phase 2 |
| Multi-cluster | ❌ → Phase 5+ |

## Deliverables

1. GitHub release `v0.1.0` (Apache 2.0)
2. Helm chart published (OCI registry)
3. Working demo video (3–5 min)
4. Docs: README, Architecture, Install, LLM Providers, Troubleshooting
5. CI: lint, test, eval-subset on every PR; nightly full eval

## Success Criteria

- Fresh user can install and complete an investigation in <15 minutes following docs alone
- RCA accuracy ≥70% on golden dataset
- Zero write capability (verified via RBAC ClusterRole audit)
- Works against 3 LTS versions of Prometheus and Loki

## Exit Gate

All Phase 1 Definition of Done items in [PHASE_1_PLAN.md §11](./PHASE_1_PLAN.md#11-definition-of-done-v010-release-checklist) checked. Tagged release. Demo published.

---

# Phase 2 — Production-Ready Analysis

**Release:** `v0.2.x`
**Detail:** Full implementation plan in [PHASE_2_PLAN.md](./PHASE_2_PLAN.md).

## Goal

Move from "tech demo" to "team uses this daily." Add the missing investigation surfaces (traces, deployments), give the agent memory of past incidents, and meet users where they live (Slack, terminal).

## New Components

| Component | Purpose |
|---|---|
| **Tracing Agent + tempo-mcp** | Distributed trace analysis, latency hotspot detection, dependency map |
| **Deployment Agent + ci-mcp** | Correlate incidents with recent deploys (Jenkins / GHA / ArgoCD) |
| **Long-Term Memory (pgvector)** | Embed past incidents; retrieve similar cases before reasoning |
| **Incident Timeline Generator** | Auto-construct timeline (deploy → first alert → root cause → resolution) |
| **Slack Bot** | `@kubepilot why is X failing?` in incident channels; reports back inline |
| **CLI** | `kubepilot investigate <service>` with `--output json` for scripting/CI |
| **Multi-tenancy (light)** | Namespace allowlists per user; RBAC for UI roles |
| **MCP adapter pattern** | Capability-based routing so users can plug in the official Grafana MCP, community k8s servers, or vendor MCPs (Datadog, etc.) without touching agent code. Our Phase 1 MCP servers become the reference implementation, not the only option. See [ARCHITECTURE.md §3.3.1](./ARCHITECTURE.md#331-why-we-ship-our-own-mcp-servers-phase-1-and-how-that-evolves-phase-2). |

## Architecture Changes

- LangGraph state grows to include `memory_context` (retrieved past incidents).
- pgvector enabled in the bundled Postgres; new embeddings table populated when an investigation concludes.
- New MCP servers (tempo-mcp, ci-mcp) added to Helm chart.
- RCA agent prompt updated to consider memory hits when reasoning.

## Success Criteria

- RCA accuracy ≥80% on golden dataset (improved by memory lookups on recurring patterns)
- Median time-to-first-byte (investigation triggered → first agent output) <5s
- Slack bot used by ≥1 external user team (validate fit beyond author)
- Timeline generator produces a correct chronology in ≥85% of test incidents

## Exit Gate

- All new components shipped + docs updated
- Eval accuracy and latency targets hit
- At least one external user has reported a real-incident win
- Release `v0.2.0` tagged

---

# Phase 3 — Enterprise-Grade Agentic SRE

**Release:** `v0.3.x`

## Goal

Make KubePilot trustworthy enough that enterprises put it in front of paying customers' production. This is the phase where **quality, evaluation, and explainability** dominate.

## New Components

| Component | Purpose |
|---|---|
| **Multi-Agent Collaboration** | Agents critique each other's findings before RCA finalizes; "debate" pattern for high-stakes incidents |
| **Cluster Knowledge Graph** | Services ↔ owners ↔ dependencies ↔ SLOs ↔ historical incidents (in Postgres + pgvector) |
| **Advanced RCA Patterns** | Runtime-specific reasoning libraries: JVM (GC, heap, thread dumps), Node (event loop, async leaks), Python (GIL, async/gunicorn), Go (goroutine leaks, GC) |
| **Evaluation Framework** | LangSmith + DeepEval + custom evals running continuously; alert when drift detected |
| **Confidence Calibration** | Calibrate model confidence to empirical accuracy; show calibration plot in AgentOps |
| **Prompt Versioning & A/B Testing** | Every prompt change tested against golden + held-out datasets before promotion |
| **Guardrails** | NeMo Guardrails / Guardrails AI on RCA outputs: forbidden recommendations, output schema enforcement, prompt-injection defense |
| **RBAC v2** | Role-based UI permissions (viewer/investigator/operator/admin), namespace-scoped tokens, audit log export to SIEM |
| **Observability Adapters** | Pluggable interface for MetricsProvider / LogsProvider; Datadog adapter as reference implementation |

## Architecture Changes

- New `critic-agent` node added; supervisor runs RCA + critic before finalizing.
- Knowledge graph queries integrated into Kubernetes Agent (it now knows "payment-service is owned by team X, depends on Y, last deployed by Z").
- Eval pipeline becomes part of the release process — releases auto-blocked on accuracy regression.

## Success Criteria

- RCA accuracy ≥90% on golden dataset
- Confidence calibration error <10% (i.e., "85% confident" = correct ~85% of the time)
- At least one Datadog-using enterprise can install and run via the adapter
- All prompts versioned; rollback of a prompt regression in <5 minutes
- ≥3 external user teams in production usage

## Exit Gate

- Accuracy + calibration + drift detection all green on the eval dashboard
- Datadog adapter shipped and tested by an external user
- Multi-agent debate produces measurably better RCAs than single-pass on a held-out set
- Release `v0.3.0` tagged

---

# Phase 4 — Autonomous Kubernetes Operations

**Release:** `v0.4.x`

## Goal

Cross the bright line from "investigates and recommends" to "executes approved remediations." Every action requires explicit human approval initially; high-confidence + low-blast-radius actions become auto-executable later in the phase.

## New Components

| Component | Purpose |
|---|---|
| **Remediation Agent** | Generates concrete kubectl/helm commands; ranks by impact + reversibility |
| **`k8s-write-mcp` server** | Separate MCP server with write verbs; ONLY deployed when remediation is enabled |
| **HITL Approval Workflow** | UI + Slack approval buttons; approval auth via OIDC; audit logged |
| **Execution Policies** | YAML policy engine: which actions allowed in which namespaces, by which roles, with which max blast radius |
| **Auto-Rollback** | If a remediation causes new errors within N minutes, automatically revert |
| **Self-Healing Loops** | For low-risk patterns (pod restart loops, ImagePullBackOff with typo, scaling within budget), auto-fix without approval — *opt-in per pattern* |
| **Post-Remediation Validation** | After executing, agent re-investigates to confirm fix worked; emits incident closure |
| **Blast-Radius Estimator** | Before any action, agent estimates impact (# of pods affected, traffic % impacted, downstream services) and shows it in approval UI |

## Architecture Changes

- Helm chart gains a feature flag: `remediation.enabled: false` by default.
- `k8s-write-mcp` runs with a distinct ServiceAccount + ClusterRole; tighter network policies.
- All write actions go through:
  1. Policy engine check
  2. Blast-radius estimate
  3. Approval gate (UI or Slack)
  4. Execution
  5. Audit log (tamper-evident)
  6. Post-validation re-investigation

## Safety Principles (Non-Negotiable)

- **Default-off:** Remediation disabled until operator opts in
- **Default-deny policies:** Empty policy file means no actions allowed
- **No silent fallbacks:** If policy unclear, fail closed and ask
- **Reversibility-aware:** Reversible actions (scale, rollout-undo) preferred; irreversible actions (delete, evict) require stronger approval
- **Blast-radius caps:** Per-policy maximum on # of pods affected per action

## Success Criteria

- ≥50% of common remediation patterns auto-suggested with executable commands
- HITL approval flow used in real incidents by ≥3 external user teams
- Auto-rollback fires correctly on injected failure scenarios (eval suite expansion)
- Zero unauthorized writes (verified by audit log diff vs approved actions)
- MTTR reduction ≥60% on real incidents tracked by participating users

## Exit Gate

- Remediation works end-to-end with HITL in `prod-small` profile
- Policy engine documentation + 5 reference policies shipped
- Auto-rollback validated against injected failures
- Post-remediation validation correctly confirms or denies fix in ≥90% of test cases
- Release `v0.4.0` tagged

---

# Beyond Phase 4 (Long Horizon)

Not committed; surfaced for vision. Each becomes a future phase only when prior phases are stable and adopted.

| Direction | What it adds |
|---|---|
| **Multi-Cluster Federation** | One KubePilot instance investigates across N clusters; cross-cluster correlation |
| **Managed Cloud Edition** | Optional SaaS for teams who don't want to self-host. Open-core monetization. |
| **Predictive SRE** | From reactive RCA to proactive: detect anomalies before they page; suggest preventive changes |
| **Custom Agent SDK** | Users author their own specialized agents (e.g., FinOps agent, Security agent) using a KubePilot SDK |
| **Compliance Auto-Reporting** | Generate SOC2 / ISO 27001 incident records from investigations automatically |
| **Integration Marketplace** | Community-contributed MCP servers for non-k8s systems (CDN, queues, data warehouses) |
| **On-Call Replacement** | First-line on-call response is the agent; humans only paged for novel/high-stakes incidents |

These will be planned in future versions of this roadmap.

---

# How Phases Compose

A user's experience evolves with each release:

```text
v0.1  → "Helps me investigate, I still fix manually"
v0.2  → "I notice it in Slack; it remembers similar past issues; faster context"
v0.3  → "I trust it. It explains its reasoning. We measure its accuracy."
v0.4  → "It fixes the obvious stuff itself. I'm paged only for novel incidents."
```

This is the progression from **AI-assisted SRE** → **AI-augmented SRE** → **Agentic SRE** → **Autonomous Operations**. Each phase must validate before the next begins. The bright line between Phase 3 and Phase 4 (no-write vs write) is the most consequential one in the project — do not cross it without complete confidence in Phase 3 quality.

---

# Phase Boundaries — Quick Reference

| Capability | P1 | P2 | P3 | P4 |
|---|:---:|:---:|:---:|:---:|
| Read k8s state | ✅ | ✅ | ✅ | ✅ |
| Query Prometheus | ✅ | ✅ | ✅ | ✅ |
| Query Loki | ✅ | ✅ | ✅ | ✅ |
| Query Tempo | | ✅ | ✅ | ✅ |
| CI/CD correlation | | ✅ | ✅ | ✅ |
| Long-term memory | | ✅ | ✅ | ✅ |
| Slack / CLI | | ✅ | ✅ | ✅ |
| Multi-agent critique | | | ✅ | ✅ |
| Knowledge graph | | | ✅ | ✅ |
| Runtime-specific reasoning | | | ✅ | ✅ |
| Eval framework | basic | basic | full | full |
| Confidence calibration | | | ✅ | ✅ |
| Prompt versioning + A/B | | | ✅ | ✅ |
| Guardrails | | basic | full | full |
| Observability adapter interface | | | ✅ | ✅ |
| Datadog adapter | | | ✅ | ✅ |
| Write to cluster | | | | ✅ |
| HITL approval | | | | ✅ |
| Auto-rollback | | | | ✅ |
| Self-healing loops | | | | ✅ |
| Multi-cluster | | | | future |
| Managed SaaS | | | | future |
