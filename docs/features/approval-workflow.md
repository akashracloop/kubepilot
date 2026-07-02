# HITL approval workflow

No remediation executes without an explicit, authorized human decision. This is
the central safety mechanism of Phase 4.

## How the gate works

The investigation graph **interrupts before the execute step** (LangGraph
`interrupt_before` + the Postgres checkpointer). When a plan is proposed, the run
pauses — durably, so it can wait hours across pod restarts — and the incident is
marked `pending_approval`. The graph resumes into execution only after an approval
is recorded; on rejection it closes without running anything.

## Approver RBAC

An approver's role must be **at least the action's required tier**:

| Action reversibility | Required tier |
|---|---|
| reversible (rollout undo/restart, scale, restart-pod, cordon) | **operator** |
| partial (edit-configmap) / any irreversible | **admin** |

`viewer` cannot approve at all. Every decision — approve, reject, and lapse — is
audited (actor role, action, resource, namespace, decision).

## Expiry

An unactioned plan **lapses** after a TTL (default 30 min) → `expired`, never a
stale auto-approval (fail-safe).

## API

```
GET  /investigations/{id}/approval        # the pending plan + blast radius + status
POST /investigations/{id}/approve         # {action_index, reason?}  (RBAC-gated)
POST /investigations/{id}/reject          # {action_index, reason?}
POST /remediation/kill-switch             # {enabled}  (admin-only) — halt everything
```

## Where you approve

- **Web UI** — the investigation page shows a *Remediation Approval* card with
  each action's reversibility, approval tier, and blast radius, plus Approve /
  Reject buttons (while pending).
- **Slack** — an approval card with Approve / Reject buttons; the action id
  encodes `decision:incident:index` so the handler routes it to the API.

## Kill switch

A single admin call (`POST /remediation/kill-switch {enabled:true}`) halts **all**
execution immediately and is audited. Every executor run checks it first.
