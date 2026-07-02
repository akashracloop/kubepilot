# Security Policy

KubePilot AI is an **agent that reads production systems**, so we take its
security posture seriously. This document covers the guarantees the project
makes, and how to report a vulnerability.

## Read-only by design (Phases 1–3)

The single most important security property: **KubePilot never writes to your
cluster** through Phase 3. This is enforced in code and reviewed, not just
documented:

- **RBAC** — the Helm `ClusterRole` grants only `get`, `list`, and `watch`.
  `services/mcp-k8s/tests/test_rbac.py` renders the chart and fails if any write
  verb appears.
- **MCP tools** — `mcp-k8s` exposes only read tools; there is no `get_secret`,
  and `get_configmap` returns keys only (never values).
- **Guardrails** — a recommendation policy (`guardrails/policy.py`) drops any
  destructive command (delete PVC/namespace/secret, `rm -rf`, `--force
  --grace-period=0`, DB drops, `helm uninstall`) before it can reach a user, and
  forces `requires_approval` on any write-shaped command.
- **Prompt-injection defense** — untrusted tool output (log lines, ConfigMap
  keys, trace data) is scrubbed of instruction-like content
  (`guardrails/sanitize.py`) before it re-enters the model's context.

Cluster writes arrive only in **Phase 4**, behind a separate `k8s-write-mcp`
server and human-in-the-loop approval.

## Handling of secrets & credentials

- **LLM API keys / DB credentials** are provided via Kubernetes Secrets (or env),
  never written to files or committed. Deployed Python containers run with
  `readOnlyRootFilesystem: true` and drop all capabilities.
- **RBAC v2** adds namespace-scoped tokens and roles (viewer / investigator /
  operator / admin); every access-controlled action is emitted as a structured
  **audit event** exportable to a SIEM via OTel.
- **BYOK / air-gapped** — no data leaves your environment unless you configure a
  cloud LLM provider; Ollama/vLLM run fully local.

## Supported versions

The project is pre-`v0.3.0`. Until the first tagged release, security fixes land
on `main`. Once releases are tagged, the latest minor version receives security
updates.

| Version | Supported |
|---|---|
| `main` (unreleased) | ✅ |
| tagged releases | latest minor once tagging begins |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

- Preferred: open a private report via GitHub → **Security → Report a
  vulnerability** (private vulnerability reporting) on this repository.
- Or email the maintainer directly: **akash.sahani@whilter.ai**.

Include, where possible: affected component/version, reproduction steps or a
proof-of-concept, impact, and any suggested remediation. Please give us a
reasonable window to investigate and ship a fix before public disclosure; we aim
to acknowledge reports within a few business days and will keep you updated on
progress. We credit reporters in the release notes unless you prefer to remain
anonymous.

## Scope

In scope: the orchestrator, api-gateway, MCP servers, Helm chart, and Web UI in
this repository. Out of scope: vulnerabilities in third-party dependencies
(report those upstream), and issues that require an already-compromised cluster
or malicious operator with existing write access.
