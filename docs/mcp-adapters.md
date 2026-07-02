# MCP Adapters — swapping the tool backends

> Phase 2. KubePilot's agents talk to **capabilities**, not to specific servers.
> This lets you point a capability at a different MCP server — the official
> Grafana MCP, a community server, or a vendor MCP — without touching agent code.

## The capability model

Each specialist agent requests a **capability domain**, not a server:

| Capability | Default server | Tools it expects |
|---|---|---|
| `kubernetes` | `mcp-k8s` | `list_pods`, `describe_pod`, `get_events`, … |
| `metrics` | `mcp-prom` | `query_metrics`, `query_range`, `query_alerts`, … |
| `logs` | `mcp-loki` | `query_logs`, `search_errors`, `search_exceptions` |
| `tracing` | `mcp-tempo` | `query_traces`, `find_failed_spans`, `service_dependency_map` |
| `deployment` | `mcp-ci` | `get_deployment_history`, `get_recent_commits`, `get_pipeline_status` |

The orchestrator resolves a capability to an `MCPClient` through a
`CapabilityRouter` (`kubepilot_orch.mcp.adapter`). KubePilot's own servers are the
**reference implementation** and remain the default; the router just stops the
agents from being hard-wired to them.

## How the swap works

The gateway builds the router from a `{capability: endpoint URL}` map. **Endpoints
that share a URL share a single client.** So to serve metrics + logs + traces from
one server (the shape of the official Grafana LGTM MCP), point all three at the
same URL:

```yaml
# values-grafana-mcp.yaml — one Grafana MCP server for metrics + logs + traces
mcp:
  prom:  { enabled: false }   # stop deploying our reference servers…
  loki:  { enabled: false }
  tempo: { enabled: false }

apiGateway:
  # …and route those three capabilities at the Grafana MCP instead.
  # (These override the gateway's KUBEPILOT_API_MCP__* endpoints.)
  extraEnv:
    KUBEPILOT_API_MCP__PROM:  "http://grafana-mcp.observability.svc.cluster.local:8080"
    KUBEPILOT_API_MCP__LOKI:  "http://grafana-mcp.observability.svc.cluster.local:8080"
    KUBEPILOT_API_MCP__TEMPO: "http://grafana-mcp.observability.svc.cluster.local:8080"
```

Because the three URLs are identical, the router creates **one** `MCPClient` and
all three agents call the same Grafana MCP server — one connection pool, one
server, three signals.

## Requirements for a replacement server

A drop-in server must:

1. Speak the same REST contract: `GET /mcp/tools`, `POST /mcp/invoke`, `GET /mcp/health`.
2. Expose tools that satisfy the capability (the agent's prompt asks for the tool
   *shapes* listed above; a server that names them differently needs its own agent
   prompt, which is a larger change).
3. Be **read-only** — KubePilot is a read-only investigator through Phase 3.

KubePilot's reference servers additionally return *curated* response shapes (e.g.
`PodSummary`, `TraceSummary`) that keep token usage down and accuracy up; a raw
passthrough server will work but may cost more tokens and reason less precisely.
See [ARCHITECTURE.md §3.3.1](./ARCHITECTURE.md#331-why-we-ship-our-own-mcp-servers-phase-1-and-how-that-evolves-phase-2).

## Community / vendor servers

The same mechanism plugs in any MCP-compatible server for a capability (community
k8s servers, a Datadog/New Relic MCP, etc.). A first-party, tested vendor adapter
(e.g. Datadog) is a Phase 3 deliverable; Phase 2 ships the pattern and proves it
with Grafana.
