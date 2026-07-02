# Remediation (Phase 4 — HITL-gated writes)

Phase 4 crosses the read→write bright line: KubePilot can **execute approved
remediations**. Every write is policy-checked, blast-radius-estimated,
human-approved, audited, and auto-rollback-guarded. It is **off by default**; the
entire write path only exists when an operator opts in.

## The gated pipeline

```
RCA → recommendation → remediation plan → [policy + blast radius]
    → HITL APPROVAL (interrupt) → execute → validate → close/rollback
```

1. **Remediation agent** turns the RCA + recommendations into an executable
   `RemediationPlan` — actions mapped to the curated write catalog, ranked
   reversible-first. It never executes; a plan outside the catalog is impossible.
2. **Policy + blast radius** — each action is gated by the default-deny
   [execution policy](execution-policies.md) and a conservative blast-radius
   estimate (pods / traffic % / dependents).
3. **HITL approval** — the graph **interrupts before executing** and waits for an
   authorized human decision (see [approval workflow](approval-workflow.md)).
4. **Execution** — the executor runs only the approved, in-policy actions via the
   `mcp-k8s-write` server, auditing each. A global **kill switch** halts everything.
5. **Validation + auto-rollback** — after executing, the fix is re-checked: an
   improvement **closes** the incident; a regression **auto-reverts** the
   reversible actions and **reopens** it.

## The write surface

`mcp-k8s-write` exposes a **curated, finite** set of reversible-leaning tools —
`rollout_undo`, `rollout_restart`, `scale`, `restart_pod`, `cordon`/`uncordon`,
`patch_image`, `edit_configmap` — with **dry-run on every tool**. There is no
arbitrary shell/kubectl. It runs under its own **least-privilege ClusterRole**
(exactly the verbs the tools need; no delete on secrets/PVC/namespaces) behind a
NetworkPolicy that only the api-gateway can reach. `test_rbac_write.py` fails if
that role ever drifts from the tool footprint.

## Turning it on (carefully)

```bash
helm upgrade kubepilot-ai ./charts/kubepilot-ai -n kubepilot-system \
  --set remediation.enabled=true \       # deploys mcp-k8s-write + the approval gate
  --set remediation.applyEnabled=false   # keep DRY-RUN until you trust it
```

- `remediation.enabled=false` (default) → no write path exists at all.
- `applyEnabled=false` → even when enabled, writes are dry-run (the executor runs
  the full gate, the write server mutates nothing).
- Provide your own policies via `remediation.policiesYaml`; an empty/missing
  policy denies **everything** (fail-closed).

Real writes are exercised only in an isolated **kind sandbox**
(`.github/workflows/remediation-e2e.yml`), never in the default CI.
