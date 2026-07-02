# Self-healing loops

For a small, fixed set of **known-safe, low-blast** patterns, KubePilot can act
**without interactive approval** — but every *other* safety gate still applies.
It is **off by default**; an operator enables each pattern individually.

## What "autonomous" does and doesn't skip

Self-healing skips **only** the interactive HITL approval. It still passes through:

- the **execution policy** (default-deny) under a configured actor role,
- the **blast-radius caps**,
- the **kill switch**,
- **per-action audit**, and
- **post-remediation validation + auto-rollback**.

So even an autonomous fix is policy-shaped, capped, audited, and reverted if it
regresses. Nothing is autonomous unless an operator turns a specific pattern on.

## Shipped patterns (all reversible)

| Pattern | Trigger | Action | Blast radius |
|---|---|---|---|
| `imagepull_revert` | RCA category `ImagePullBackOff` (usually a bad tag) | `rollout_undo` to the last-good revision | workload |
| `crashloop_restart` | a crash-looping pod with a transient cause | `restart_pod` (controller recreates it) | 1 pod |

`DEFAULT_ENABLED` is empty — no pattern acts until enabled. A disabled pattern
never matches, and a non-matching category produces no action.

## Enabling

Self-healing is enabled per pattern by name (comma-separated), and only when
remediation itself is on:

```bash
helm upgrade kubepilot-ai ./charts/kubepilot-ai -n kubepilot-system \
  --set remediation.enabled=true \
  --set-string remediation.selfhealPatterns="imagepull_revert,crashloop_restart" \
  --set-string remediation.selfhealRole=operator      # actor role the action runs as
```

(env equivalent: `KUBEPILOT_API_REMEDIATION_SELFHEAL_PATTERNS`.) When set and an
incident matches an enabled pattern, the graph routes to an autonomous node
**instead of** the HITL interrupt; with no patterns the shape is unchanged and
every remediation waits for approval.

Because the action runs through the executor under the actor role, the execution
policy must also permit it — enabling a pattern is necessary but not sufficient;
the policy still has the final say. And with `applyEnabled=false` an autonomous
fix is still a dry-run, exactly like a human-approved one.

Self-healing is the last, most-earned capability in Phase 4: use it only for
patterns you have watched succeed under HITL, and keep the kill switch handy.
