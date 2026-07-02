# RBAC v2 + audit export

Role-based access control with namespace-scoped tokens and SIEM audit export.

## Roles

A presented API key resolves to a `Principal { role, namespaces }`. Roles, in
ascending privilege:

| Role | Can view | Can trigger | Namespace scope | Admin endpoints |
|---|---|---|---|---|
| `viewer` | ✅ | ❌ | scoped | ❌ |
| `investigator` | ✅ | ✅ | scoped | ❌ |
| `operator` | ✅ | ✅ | **all** (scope lifted) | ❌ |
| `admin` | ✅ | ✅ | **all** | ✅ |

`operator` and `admin` transcend namespace scoping — an on-call operator needs the
whole cluster picture. `viewer`/`investigator` are restricted to their
`namespaces` set (empty = all). An unknown role is denied everything (deny by
default).

Configure per-key policies via `KUBEPILOT_API_AUTH__KEYS` (JSON):

```json
{ "<secret>": { "role": "operator", "namespaces": ["prod"] } }
```

With no key configured at all, the gateway runs in **open dev mode** (investigator,
all namespaces). OIDC/Keycloak is the planned opt-in backend — it issues
short-lived tokens that map to the same `Principal` shape; the static-token path
stays primary.

## Audit export

Every access-controlled action emits a structured **audit event** — actor role,
action, resource, namespace, and the allow/deny decision — through a dedicated
`kubepilot.audit` logger that the AgentOps OTel pipeline exports to a SIEM.

**Denials are audited too**: a namespace-scoped principal reaching for another
namespace produces a `denied` record with a `reason`, which is exactly what a
security team wants to see. A full investigation's create/read actions appear in
the exported log with the acting role and namespace.
