# Observability adapters (Datadog reference)

KubePilot's MCP **capability router** maps a capability domain (`metrics`, `logs`,
`tracing`, …) to an MCP server URL. Because every MCP server returns KubePilot's
**curated response shapes** — not raw backend objects — swapping the backend for a
capability is a config-only change. Phase 3 ships a **Datadog** reference adapter
to prove the seam beyond the Grafana stack.

## How the adapter interface works

The "adapter interface" is the set of curated capability shapes the orchestrator
consumes: `MetricSeries`/`Sample` for metrics, `LogLine` for logs (identical
across mcp-prom, mcp-loki, and mcp-datadog). An adapter maps its backend **into
these shapes** — never the reverse — so the orchestrator sees one contract
regardless of backend.

## mcp-datadog

`services/mcp-datadog` implements the standard MCP REST contract
(`/mcp/tools`, `/mcp/invoke`, `/mcp/health`) over the Datadog API:

- `query_metrics` — maps a Datadog timeseries query (`/api/v1/query` `pointlist`)
  into curated `MetricSeries` (scope → labels, epoch-ms → tz-aware samples, null
  points dropped);
- `search_logs` — maps a Datadog Logs Search (`/api/v2/logs/events/search`) into
  curated `LogLine` rows (attributes → `service`/`status`/`host` labels).

It reads `DD_API_KEY` / `DD_APP_KEY` / `DD_SITE` and is **read-only by
construction** — only query/search tools are exposed (a contract test asserts no
write tools).

## Running an investigation via Datadog

Point the `metrics` and `logs` capabilities at the Datadog server — no agent or
prompt change:

```yaml
mcp:
  prom: http://kubepilot-ai-mcp-datadog:8080   # metrics capability → Datadog
  loki: http://kubepilot-ai-mcp-datadog:8080   # logs capability → Datadog
```

The Kubernetes specialist still uses `mcp-k8s`; metrics and logs now come from
Datadog, curated into the same shapes the RCA agent already reasons over.
