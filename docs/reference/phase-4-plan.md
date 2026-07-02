# KubePilot AI — Phase 4 Implementation Plan

> **Goal:** Cross the bright line from *"investigates and recommends"* to
> *"executes approved remediations."* Phase 1 proved the loop; Phase 2 made it a
> daily tool; Phase 3 made it trustworthy and measurable. **Phase 4 is where
> KubePilot acts on the cluster** — but every write is policy-checked,
> blast-radius-estimated, human-approved, audited, and auto-rollback-guarded.
> High-confidence + low-blast-radius patterns become opt-in self-healing *later*
> in the phase, never by default.

> Reference: [architecture.md](./architecture.md) (the *what*), this doc (the
> *how* for v0.4.x), [roadmap.md](./roadmap.md) (the *when*),
> [phase-3-plan.md](./phase-3-plan.md) (what shipped in v0.3.x and is assumed
> here). **Do not start Phase 4 until Phase 3 is tagged, demoed, and its accuracy
> + calibration + guardrails are proven in production.** The read→write line is
> the most consequential decision in the project; cross it only with complete
> confidence in Phase 3 quality.

---

## 0. What Phases 1–3 Give Us (Starting Point)

Phase 4 extends real seams built to be extended. Nothing here is greenfield:

| Existing artifact | Phase 4 extends it by… |
|---|---|
| Recommendation agent → structured `Recommendation` (commands, risk, reversibility, `requires_approval`) | A **Remediation agent** that produces *executable* `RemediationAction`s from the same shape |
| Guardrails `policy.py` (forbidden/destructive rec blocking) | A full **execution policy engine** (default-deny; who/what/where/blast-radius) layered on top |
| MCP capability router (domain → server, config-only) | A **separate `mcp-k8s-write`** server with write verbs, deployed **only** when remediation is enabled |
| RBAC v2 (viewer/investigator/operator/admin, namespace scoping) + audit export | **Approval authority** (who may approve/execute) + a **tamper-evident** action audit |
| LangGraph + Postgres checkpointer (resumable across restarts) | **Interrupt-before-execute**: the graph pauses at the approval gate and resumes on human decision |
| Web UI (renders escalate/critique/knowledge) + Slack bot | **Approval UI + Slack approve/reject buttons** |
| Eval harness (golden + held-out + drift + release gate) | **Remediation + rollback + post-validation** eval suites; a kind execution sandbox |
| Helm umbrella chart (`readOnlyRootFilesystem`, RBAC get/list/watch) | A gated `remediation.enabled` flag + a distinct write ServiceAccount/ClusterRole + NetworkPolicies |

**Release:** `v0.4.x`. **Action posture:** **Observe + Act (with approval).**
Zero autonomous writes until an operator opts in per pattern.

---

## 1. Success Criteria

Phase 4 is **done** when all of these are true:

1. **HITL remediation works end-to-end** in the `prod-small` profile: RCA →
   remediation plan → policy check → blast-radius estimate → approval (UI or
   Slack) → execution → post-validation → incident closure — with a full audit
   trail and no manual `kubectl`.
2. **Default-off, default-deny.** With `remediation.enabled=false` (the default),
   no write path exists at all (`mcp-k8s-write` isn't deployed). With it enabled
   but an empty policy file, **every** action is denied (fail-closed).
3. **Policy engine** gates every write by role × action × namespace × max
   blast-radius, with 5 shipped reference policies and docs.
4. **Blast-radius estimation** precedes every action and is shown in the approval
   UI (pods affected, traffic %, downstream services), within a stated tolerance.
5. **Reversibility-aware execution:** reversible actions (scale, rollout-undo,
   restart) are preferred; irreversible actions (delete, evict, drain) require a
   stronger approval tier and never auto-execute.
6. **Auto-rollback** fires correctly on injected failures: if a remediation
   introduces new errors within N minutes, the reversible action is
   automatically reverted and the incident re-opened.
7. **Post-remediation validation** re-investigates and correctly confirms or
   denies the fix in **≥90%** of test cases.
8. **Zero unauthorized writes** — an audit-diff test proves every executed action
   maps to an approved, in-policy decision (no silent or out-of-band writes).
9. **Self-healing is opt-in per pattern** (pod restart loops, ImagePullBackOff
   from a typo revert, scale-within-budget); disabled by default; still policy-
   checked + audited; instantly disable-able via a global kill switch.
10. **Kill switch**: a single flag (and API/CLI call) halts all execution
    immediately and drains pending approvals.
11. **≥3 external user teams** run HITL remediation on real incidents; **MTTR
    reduction ≥60%** on tracked incidents; `v0.4.0` tagged + demoed.

---

## 2. Scope

### 2.1 In Scope

| Item | Detail |
|---|---|
| Remediation agent | Turns the RCA + recommendations into ranked, executable `RemediationAction`s (kubectl/helm), reversibility- and impact-aware |
| `mcp-k8s-write` server | Separate MCP with a curated, *minimal* set of write tools (rollout undo/restart, scale, cordon/uncordon, delete-pod, patch-image, edit-configmap); dry-run built in |
| Execution policy engine | YAML: allowed actions × namespaces × roles × max blast-radius; default-deny; reversibility tiers; per-policy caps |
| Blast-radius estimator | Pre-flight impact estimate (pods, traffic %, dependents) from the k8s API + the knowledge graph |
| HITL approval workflow | Graph interrupt-before-execute; approve/reject API; approver RBAC; expiry; audit |
| Approval UI + Slack | Approval cards with the plan, blast radius, diff, and reversibility; buttons |
| Execution engine | Runs approved, in-policy actions via `mcp-k8s-write`; per-action audit; kill switch; dry-run mode |
| Auto-rollback | Watch post-execution signals; revert reversible actions on regression within N minutes |
| Post-remediation validation | Re-investigate to confirm the fix; emit incident closure or re-open |
| Self-healing loops | Opt-in-per-pattern autonomous fixes for known-safe, low-blast-radius cases; still gated + audited |
| Safety hardening | Distinct write ServiceAccount/ClusterRole (still least-privilege), NetworkPolicies, tamper-evident audit, dry-run, chaos eval |

### 2.2 Out of Scope (deferred to Phase 5+)

- **Fully autonomous remediation by default** — self-healing stays opt-in per
  pattern; broad autonomy is a later, separately-earned step.
- Multi-cluster remediation / federation.
- Managed SaaS control plane; predictive/proactive remediation.
- A general "run any command" tool — the write surface is a **curated, finite**
  allow-list, never arbitrary shell/kubectl.
- Non-k8s writes (CD systems, cloud APIs, databases).

---

## 3. Repository Structure (Additions)

```text
services/
├── mcp-k8s-write/                  (NEW — write MCP; deployed only when enabled)
│   ├── src/mcp_k8s_write/
│   │   ├── server.py               (REST MCP contract; dry-run flag on every tool)
│   │   ├── tools/                  (rollout_undo, rollout_restart, scale, cordon,
│   │   │                            uncordon, delete_pod, patch_image, edit_configmap)
│   │   └── safety.py               (per-tool reversibility + required approval tier)
│   ├── tests/test_write_tools.py   (dry-run assertions, no real cluster)
│   └── tests/test_rbac_write.py    (renders chart; asserts the write ClusterRole is minimal)
│
orchestrator/src/kubepilot_orch/
├── agents/remediation_agent.py     (NEW — executable RemediationAction plan)
├── remediation/                    (NEW)
│   ├── policy.py                   (YAML policy engine; default-deny; blast-radius caps)
│   ├── blast_radius.py             (impact estimate from k8s API + knowledge graph)
│   ├── executor.py                 (policy → approval → mcp-k8s-write → audit)
│   ├── rollback.py                 (auto-revert on post-exec regression)
│   ├── validation.py               (post-remediation re-investigation + closure)
│   └── selfheal.py                 (opt-in-per-pattern autonomous loops)
├── policies/                       (NEW — 5 shipped reference policies)
│   └── *.yaml
└── state.py                        (v3 → v4 additive: remediation plan/approvals/executions)

services/api-gateway/…/routes/approvals.py   (NEW — approve/reject, list pending, kill switch)
services/web-ui/…/approvals/                 (NEW — approval UI)
services/slack-bot/…/approvals.py            (NEW — Slack approve/reject buttons)

charts/kubepilot-ai/
├── templates/mcp-k8s-write-*.yaml           (deployment/service/serviceaccount/clusterrole — gated)
├── templates/remediation-policies-configmap.yaml
└── values*.yaml                             (remediation.enabled: false by default)

eval/harness/
├── remediation_eval.py             (NEW — plan quality on golden incidents)
├── rollback_eval.py                (NEW — rollback fires on injected regressions)
└── validation_eval.py              (NEW — post-validation confirm/deny accuracy)

docs/features/  remediation.md · approval-workflow.md · execution-policies.md · self-healing.md
.github/workflows/  remediation-e2e.yml       (NEW — kind sandbox: approve → execute → rollback)
```

---

## 4. Milestones

~12 weeks + a hard buffer for one full-time engineer. Safety work is not
compressible — do not cut the sandbox, rollback, or audit-diff milestones.

| Week | Milestone | Deliverable | Verification |
|---|---|---|---|
| **W1** | State v4 + write-MCP scaffold | Additive v4 fields (+ fixture); `mcp-k8s-write` with **dry-run-only** tools behind a hard off switch | Fixture-replay v1–v4; write server returns dry-run plans, never executes |
| **W2** | Execution policy engine | `policy.py` (default-deny; role × action × namespace × blast-radius); 5 reference policies | Policy matrix test; empty policy denies everything |
| **W3** | Blast-radius estimator | `blast_radius.py` from k8s API + knowledge graph | Estimate within tolerance on fixture clusters |
| **W4** | Remediation agent | Executable `RemediationAction` plan, reversibility/impact-ranked, guardrail-filtered | Unit: destructive actions flagged irreversible + high tier |
| **W5** | HITL approval (backend) | Graph **interrupt-before-execute**; approve/reject API; approver RBAC; expiry | Graph pauses; resumes only on an authorized approval |
| **W6** | Approval UI + Slack | Approval cards (plan + blast radius + diff + reversibility) + buttons | A human approves via UI and via Slack in a demo |
| **W7** | Execution engine | Runs approved+in-policy actions via write-MCP; per-action audit; dry-run mode; **kill switch** | Kill switch halts execution mid-flight; audit records every action |
| **W8** | Auto-rollback | `rollback.py` reverts reversible actions on post-exec regression within N min | Injected regression → automatic revert + incident re-open |
| **W9** | Post-remediation validation | `validation.py` re-investigates + emits closure/re-open | Confirms/denies fix ≥90% on the validation eval |
| **W10** | Self-healing (opt-in) | `selfheal.py` per-pattern autonomous loops (restart / imagepull-typo / scale-in-budget); off by default | Enabled pattern fixes a seeded incident; disabled patterns never act |
| **W11** | Safety hardening | Write ServiceAccount/ClusterRole (least-privilege) + NetworkPolicies + tamper-evident audit; kind e2e | `remediation-e2e.yml`: approve → execute → rollback in kind; no unauthorized-write audit diff |
| **W12** | MTTR + docs + release | ≥3 external teams; MTTR −60%; all docs; `v0.4.0` tagged + demo | Real-incident metrics; the demo runs unattended first-try |

**Buffer week (W13).** Do not skip. Safety regressions found late cost the most.

---

## 5. Component Deliverables (Detail)

### 5.1 Remediation Agent (`agents/remediation_agent.py`)
Consumes the RCA + `Recommendation`s and produces a ranked `RemediationPlan` of
`RemediationAction`s — each an *executable* command mapped to a specific
`mcp-k8s-write` tool, carrying `reversibility`, an estimated blast radius, the
minimum `approval_tier`, and a `dry_run_preview`. It runs the Phase 3 guardrail
policy first, so nothing destructive ever reaches a plan. **The agent never
executes** — it only proposes; execution is a separate, gated step.

### 5.2 `mcp-k8s-write` Server (`services/mcp-k8s-write`)
A *separate* MCP server with a **curated, finite** write surface — no arbitrary
shell. Tools: `rollout_undo`, `rollout_restart`, `scale`, `cordon`/`uncordon`,
`delete_pod`, `patch_image`, `edit_configmap`. **Every tool supports `dry_run`**
(server-side, using the k8s API `dryRun=All`) and returns the would-be diff. It
runs under its **own ServiceAccount + ClusterRole** (still least-privilege — only
the verbs the tools need, only in allowed namespaces), and is **only rendered by
Helm when `remediation.enabled=true`**. `test_rbac_write.py` asserts the write
role grants no verb beyond what the tools require.

### 5.3 Execution Policy Engine (`remediation/policy.py`)
A YAML policy engine, **default-deny**: an action executes only if a policy
explicitly allows it for that role × action × namespace, under a per-policy
**max blast-radius** and **reversibility tier**. Empty/unclear policy → deny and
ask (fail-closed, no silent fallback). 5 reference policies ship (e.g.
"restart-only in dev", "scale-within-budget in prod for operators",
"rollout-undo with approval"). Policies are a ConfigMap; a bad policy file fails
validation at load, not at execution.

### 5.4 Blast-Radius Estimator (`remediation/blast_radius.py`)
Before any action, estimate impact from the live k8s API + the Phase 3 knowledge
graph: pods affected, approximate traffic %, and downstream **dependents** (so
"restart payments-db" surfaces that checkout rides on it). Feeds the policy caps
and the approval UI. Estimates are conservative (over-estimate impact).

### 5.5 HITL Approval Workflow (`routes/approvals.py` + graph interrupt)
The graph **interrupts before execution** (LangGraph interrupt + the Postgres
checkpointer already persists state), surfacing a pending approval. Approve/reject
is an API call gated by **approver RBAC** (operator+ for reversible, admin for
irreversible), with an **expiry** (unapproved plans lapse). Approval, rejection,
and expiry are all audited. On approval the graph resumes into execution; on
rejection it closes with the recommendation recorded but not run.

### 5.6 Execution Engine (`remediation/executor.py`)
The only path to a write: **policy check → blast-radius gate → approval →
`mcp-k8s-write` invoke → per-action audit**. Supports a global **dry-run mode**
(execute nothing, log the diffs) and a **kill switch** that halts execution and
drains pending approvals. Every executed action writes a tamper-evident audit
record linking it to its approval + policy decision.

### 5.7 Auto-Rollback (`remediation/rollback.py`)
After a reversible action, watch the same signals the investigation used
(errors, restarts, latency) for N minutes. On a regression attributable to the
action, **automatically revert** it (rollout-undo, scale-back, uncordon), audit
the rollback, and re-open the incident. Irreversible actions are never
auto-taken, so there is nothing unsafe to auto-revert.

### 5.8 Post-Remediation Validation (`remediation/validation.py`)
After execution (and any rollback), the agent **re-investigates** the same
incident and decides: fixed (emit closure) or not (re-open with the new
evidence). This closes the loop and produces the incident record used for MTTR
metrics and the validation eval.

### 5.9 Self-Healing Loops (`remediation/selfheal.py`)
**Opt-in per pattern**, off by default. For a small set of known-safe, low-blast
patterns — pod restart loops, ImagePullBackOff from a typo (revert the image),
scale-within-budget — the loop may act **without** interactive approval, but
still **within policy, within blast-radius caps, fully audited**, and subject to
auto-rollback + the kill switch. Each pattern is enabled individually by an
operator; nothing is autonomous by default.

---

## 6. Architecture Changes

- **Graph:** `… rca → critic → recommendation → remediation-plan → [INTERRUPT:
  approval] → execute → auto-rollback watch → post-validation → finalize`. The
  approval interrupt uses the existing checkpointer so a plan can wait hours for a
  human across pod restarts.
- **State v3 → v4 (additive):** `remediation_plan: RemediationPlan | None`,
  `approvals: list[Approval]`, `executions: list[ExecutionRecord]`,
  `rollbacks: list[RollbackRecord]`, `remediation_outcome: str | None`. Additive →
  trivial `_v3_to_v4` version-stamp + a v4 fixture; fixture-replay loads v1–v4.
- **New capability domain:** `Capability.WRITE` → `mcp-k8s-write` (only present
  when enabled), kept strictly separate from the read capabilities.
- **Helm:** `remediation.enabled: false` by default; when true, renders
  `mcp-k8s-write` (distinct SA/ClusterRole + NetworkPolicies), the policies
  ConfigMap, and the approval routes. When false, **none** of the write path
  exists in the cluster.
- **RBAC:** approver tiers layered on RBAC v2 (operator approves reversible; admin
  approves irreversible); execution is a distinct audited action.

---

## 7. Eval & Safety Strategy (Phase 4)

- **Kind execution sandbox** (`remediation-e2e.yml`): a throwaway cluster where
  approve → execute → rollback runs for real against seeded failures — the only
  place writes are exercised end-to-end in CI.
- **Remediation eval**: on golden incidents, the plan proposes the correct,
  reversible-first action with a sane blast-radius.
- **Rollback eval**: inject a regression after a remediation; assert auto-rollback
  fires and reverts within N minutes.
- **Validation eval**: post-remediation re-investigation confirms/denies the fix
  ≥90%.
- **Policy matrix**: role × action × namespace × blast-radius — allow/deny is
  exactly as specified; empty policy denies all.
- **Audit-diff invariant**: for every run, executed actions == approved,
  in-policy actions. Any divergence fails the build.
- **Chaos**: randomized approval timing, kill-switch mid-execution, write-MCP
  unavailable → the system fails **closed**, never open.

---

## 8. Testing Strategy

| Layer | Phase 4 additions |
|---|---|
| Unit | Policy decisions; blast-radius math; remediation ranking; rollback trigger logic; approval RBAC; kill switch |
| Contract | `mcp-k8s-write` tool schemas + **dry-run** posture; write ClusterRole is minimal (`test_rbac_write.py`) |
| Integration | Approval interrupt/resume across a checkpointer restart; executor → write-MCP (mocked k8s) |
| End-to-end | Kind sandbox: seed failure → plan → approve → execute → validate → (inject regression) → rollback |
| Eval | Remediation plan quality, rollback fires, validation ≥90%, audit-diff invariant |

Keep ≥70% line coverage on the new modules. **Never** exercise real writes in the
default CI job — only in the isolated kind sandbox. `ScriptedLLM` can't see write
side-effects, so validate the executor + rollback against the sandbox, not just
scripted tests.

---

## 9. Demo Acceptance Criteria (v0.4.0)

1. An incident is investigated; the agent proposes a **reversible** remediation
   with its **blast radius** shown; an operator **approves in the UI**; it
   executes; post-validation confirms the fix; the incident closes — all audited.
2. The **same** flow via **Slack** approve/reject buttons.
3. An **irreversible** action (e.g. delete PVC) is **refused by policy** and never
   offered as executable.
4. A remediation that makes things worse triggers **auto-rollback** within N
   minutes and re-opens the incident.
5. A seeded ImagePullBackOff-typo is fixed by an **opt-in self-healing** pattern —
   and the same pattern, when disabled, does nothing.
6. The **kill switch** halts an in-flight execution and drains pending approvals.
7. The audit log shows **zero** writes without a matching approval + policy
   decision.

If any step needs manual `kubectl` or fails first-try, Phase 4 isn't done.

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A bad write causes an outage | Med | **Critical** | Default-off, default-deny, reversibility-first, blast-radius caps, auto-rollback, kind sandbox, dry-run everywhere |
| Policy misconfiguration allows too much | Med | High | Fail-closed defaults, policy validation at load, 5 reviewed reference policies, audit-diff invariant |
| Approval fatigue → rubber-stamping | High | Med | Rich approval cards (blast radius + diff + reversibility), sensible batching, opt-in self-heal for the truly-safe |
| Blast-radius estimate wrong (under-counts) | Med | High | Conservative over-estimation; treat estimate as a floor; caps enforced independently |
| Self-healing acts on a novel/unsafe case | Low | Critical | Opt-in per pattern, tight allow-list, policy + caps + audit + auto-rollback + kill switch still apply |
| Compromised approver / stolen token | Low | Critical | Approver RBAC tiers, short-lived tokens (OIDC), tamper-evident audit, per-action least-privilege write role |
| LLM proposes a subtly-wrong command | Med | High | Curated finite tool surface (no free-form shell), dry-run diff shown before approval, human in the loop |
| Auto-rollback loops (revert → re-fail) | Low | Med | Rollback once, then escalate to human; never auto-retry a failed remediation |

---

## 11. Definition of Done (v0.4.0 Release Checklist)

- [ ] HITL remediation end-to-end in `prod-small` (plan → policy → blast radius → approve → execute → validate → close), fully audited
- [ ] `remediation.enabled=false` by default; enabled+empty-policy denies everything (fail-closed)
- [ ] `mcp-k8s-write` shipped: curated write tools, dry-run on every tool, distinct least-privilege ClusterRole (`test_rbac_write.py` green)
- [ ] Policy engine + 5 reference policies + docs; policy matrix test
- [ ] Blast-radius estimator shown in the approval UI, within tolerance
- [ ] Approval workflow (UI + Slack) with approver RBAC + expiry
- [ ] Execution engine with per-action audit, dry-run mode, and a working **kill switch**
- [ ] Auto-rollback fires on injected regressions (rollback eval)
- [ ] Post-remediation validation ≥90% (validation eval)
- [ ] Self-healing opt-in per pattern; disabled by default; gated + audited
- [ ] **Zero unauthorized writes** — audit-diff invariant green in CI + kind sandbox
- [ ] State schema v4 (additive) with v1–v4 fixture-replay green
- [ ] Docs: remediation, approval-workflow, execution-policies, self-healing + updated architecture
- [ ] CI green + the kind `remediation-e2e` sandbox; MTTR reduction ≥60% on tracked incidents
- [ ] **≥3 external user teams** running HITL remediation on real incidents
- [ ] GitHub release `v0.4.0` with changelog + demo video

---

## 12. After Phase 4

When every box is green, KubePilot has completed the arc from **AI-assisted →
AI-augmented → Agentic → Autonomous Operations**. Beyond Phase 4 (multi-cluster
federation, managed SaaS, predictive SRE, a custom-agent SDK, compliance
auto-reporting, on-call replacement) is vision, not commitment — see
[roadmap.md](./roadmap.md). Each becomes a phase only when the prior ones are
stable and adopted. The write capability earned here is the most powerful and the
most dangerous in the project; every extension of it must clear the same
default-off, default-deny, human-approved, audited, reversible bar.
