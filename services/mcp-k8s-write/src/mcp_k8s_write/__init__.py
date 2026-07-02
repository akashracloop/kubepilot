"""KubePilot AI write MCP server (Phase 4).

The ONLY component in KubePilot that can mutate a cluster - and it is deliberately
constrained:

- **Deployed only when `remediation.enabled=true`** in Helm. When remediation is
  off, this server does not exist in the cluster.
- **Curated, finite write surface** - a fixed allow-list of reversible-leaning
  tools (rollout undo/restart, scale, cordon/uncordon, delete-pod, patch-image,
  edit-configmap). There is no arbitrary shell/kubectl.
- **Dry-run by default.** Real application is gated behind a hard off switch
  (`KUBEPILOT_WRITE_APPLY_ENABLED`, default false) AND the orchestrator's policy →
  blast-radius → HITL-approval pipeline. In Phase 4 W1 this server is dry-run
  ONLY - every invoke returns the would-be change and applies nothing.

Same REST MCP contract as the read servers (`/mcp/tools`, `/mcp/invoke`,
`/mcp/health`).
"""

from __future__ import annotations

__version__ = "0.1.0-dev"
