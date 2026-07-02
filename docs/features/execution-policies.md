# Execution policies

The execution policy engine decides whether an approved action may actually run.
It is **default-deny / fail-closed**: an action executes only if a policy rule
explicitly allows it. An empty or missing policy file denies **everything**.

## Policy shape (YAML)

```yaml
policies:
  - name: prod-reversible-rollback
    roles: [operator, admin]      # RBAC roles that may execute
    namespaces: [prod]            # explicit list, or ["*"]
    actions: [rollout_undo, rollout_restart, patch_image]
    reversibility: [reversible]   # tiers this rule authorizes
    max_blast_radius:             # per-rule caps (optional)
      pods: 50
      traffic_percent: 100
```

An action is allowed only if some rule matches **all** of: role, namespace,
action, reversibility tier, **and** the blast-radius is within the rule's caps.
`"*"` is an explicit opt-in wildcard — there are no implicit ones. A malformed
policy fails at load, not at execution time.

## The gate order

For every approved action the executor checks, in order:

1. **kill switch** — halt everything if active
2. **policy** — default-deny match (role × action × namespace × reversibility)
3. **blast-radius caps** — reject if over the rule's `max_blast_radius`
4. **execute** via `mcp-k8s-write`, then **audit** the outcome

Anything that fails a gate is recorded as `skipped` with the reason and audited —
never silently dropped. Self-healing actions pass through the **same** gates under
a configured actor role; they only skip the interactive approval.

## Shipped reference policies

Five reference policies ship in `orchestrator/.../policies/` (dev restart/scale,
prod reversible rollback, prod scale-within-budget, node cordon ops, admin-only
configmap edits). `default_policies()` loads them. Override in-cluster with
`remediation.policiesYaml` (rendered into a ConfigMap the gateway reads).

## Zero unauthorized writes

The executor only runs actions that are **both** approved and in-policy, and
audits every attempt. Because execution flows exclusively through this gate, the
audit log is the ground truth: executed actions == approved, in-policy actions.
