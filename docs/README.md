# KubePilot AI — Documentation

Start here. Docs are grouped by what you're trying to do.

## 🚀 Getting started

| Doc | What it covers |
|---|---|
| [getting-started/minikube.md](./getting-started/minikube.md) | **Run the whole platform locally on minikube** in one command (OpenAI `gpt-4o-mini`). Best first stop. |
| [getting-started/install.md](./getting-started/install.md) | Local dev + Helm install into any cluster; profiles (dev / prod-small / prod-air-gapped). |
| [getting-started/troubleshooting.md](./getting-started/troubleshooting.md) | Symptom → cause → fix for common issues. |

## ⚙️ Configuration

| Doc | What it covers |
|---|---|
| [configuration/llm-providers.md](./configuration/llm-providers.md) | BYOK + local models: Anthropic, OpenAI, Bedrock, Azure, Ollama, vLLM; per-role routing. |
| [configuration/mcp-adapters.md](./configuration/mcp-adapters.md) | Swap the tool backends (e.g. point metrics/logs/traces at one Grafana MCP). |

## ✨ Features

| Doc | What it covers |
|---|---|
| [features/memory.md](./features/memory.md) | Long-term incident memory (pgvector): retrieve-before-RCA, embed-on-finalize. |
| [features/tracing-and-ci.md](./features/tracing-and-ci.md) | Tracing (Tempo) + Deployment (CI) specialists and their MCP servers. |
| [features/slack.md](./features/slack.md) | The Slack bot: `@kubepilot why is X failing?`. |
| [features/cli.md](./features/cli.md) | The `kubepilot` CLI for terminal / CI workflows. |

## 📐 Reference & planning

| Doc | What it covers |
|---|---|
| [reference/architecture.md](./reference/architecture.md) | The engineering view: components, data flow, MCP, memory, security, deployment. |
| [reference/roadmap.md](./reference/roadmap.md) | All four phases at a glance. |
| [reference/phase-1-plan.md](./reference/phase-1-plan.md) | Phase 1 (MVP) implementation plan + Definition of Done. |
| [reference/phase-2-plan.md](./reference/phase-2-plan.md) | Phase 2 (production-ready) implementation plan + Definition of Done. |

---

Product vision lives in [../IDEA.md](../IDEA.md); contribution guide in
[../CONTRIBUTING.md](../CONTRIBUTING.md).
