# KubePilot AI
## Open Source Agentic SRE Platform for Kubernetes

> Your AI SRE teammate for Kubernetes, Cloud-Native Infrastructure, and Production Incident Management.

---

# Vision

Modern engineering teams spend significant time diagnosing and resolving production incidents. Root cause analysis often requires switching between Kubernetes dashboards, logs, metrics, traces, deployment history, and monitoring tools.

KubePilot AI aims to act as an autonomous Site Reliability Engineer (SRE) capable of investigating production issues, correlating signals across systems, identifying root causes, recommending remediations, and eventually executing approved corrective actions.

The goal is not to build another Kubernetes chatbot.

The goal is to build an intelligent Agentic SRE Platform.

---

# Workload Scope

KubePilot AI is **workload-agnostic**. It investigates any service running on Kubernetes — Java (Spring Boot, JVM), Python (Django, Flask, FastAPI), Node.js, Go, .NET, Ruby, databases, message queues, or any containerized workload.

The platform reasons about **Kubernetes-level signals** (pods, events, metrics, logs, traces, deployments) which apply to all workloads equally. Language- or runtime-specific intelligence (e.g., JVM OOM patterns, Node.js event loop stalls, Python GIL contention, Go goroutine leaks) is layered on through the RCA agent's knowledge base — not hardcoded per language.

The "AI" in KubePilot AI refers to the *agent doing the investigation*, not the *type of workload being investigated*. This is **not** an LLMOps or ML-monitoring tool.

---

# Problem Statement

When a production issue occurs, engineers typically perform the following steps manually:

```text
Pod CrashLoopBackOff
       ↓
Check kubectl events
       ↓
Inspect logs
       ↓
Check Prometheus metrics
       ↓
Analyze Grafana dashboards
       ↓
Review deployment history
       ↓
Inspect ConfigMaps/Secrets
       ↓
Determine root cause
```

This process:

- Consumes significant engineering time
- Requires experienced SREs
- Delays incident resolution
- Increases operational costs
- Creates knowledge silos

KubePilot AI automates this investigation workflow using autonomous agents.

---

# Objectives

## Primary Goals

- Autonomous production incident investigation
- Kubernetes root cause analysis
- Multi-agent reasoning
- Observability correlation
- Incident summarization
- Remediation recommendation

## Secondary Goals

- Incident timeline generation
- Knowledge retention
- Agent memory
- Human-in-the-loop approvals
- Autonomous remediation

---

# Target Users

## Primary

- DevOps Engineers
- Platform Engineers
- Site Reliability Engineers (SREs)
- Cloud Engineers
- Kubernetes Administrators

## Secondary

- Startups
- SaaS Companies
- Managed Service Providers
- Enterprise Platform Teams

---

# Example Workflow

## User Query

```text
Why is payment-service failing?
```

## KubePilot Investigation

```text
✓ Retrieved pod status
✓ Retrieved pod logs
✓ Retrieved deployment history
✓ Retrieved cluster events
✓ Retrieved Prometheus metrics
✓ Retrieved Loki logs
✓ Retrieved Tempo traces
✓ Retrieved recent Jenkins deployments
```

## Agent Response

```text
Root Cause:
OOMKilled due to memory leak introduced in deployment v1.24.8

Confidence:
92%

Evidence:
- Memory consumption increased 300%
- Pod restarted 12 times
- Deployment occurred 8 minutes before failure

Recommended Actions:
1. Rollback deployment
2. Increase memory limit to 2Gi
3. Investigate cache growth issue
```

---

# System Architecture

```text
                    User

                      │

                      ▼

            LangGraph Supervisor

                      │

        ┌─────────────┼─────────────┐
        │             │             │

        ▼             ▼             ▼

 Kubernetes      Observability    Deployment
    Agent           Agents          Agent

        │             │             │

        ▼             ▼             ▼

      K8s      Loki/Tempo/Prom    Jenkins

                      │

                      ▼

               RCA Agent

                      │

                      ▼

           Recommendation Agent

                      │

                      ▼

             Remediation Agent
```

---

# Core Components

## 1. Supervisor Agent

### Responsibilities

- Task orchestration
- Agent coordination
- Workflow execution
- Result aggregation
- Confidence scoring

### Framework

- LangGraph

---

# 2. Kubernetes Agent

### Responsibilities

- Pod inspection
- Deployment analysis
- StatefulSet analysis
- Service inspection
- Node health checks
- Event collection

### Available Tools

```python
get_pods()
describe_pod()
get_events()
get_nodes()
get_deployments()
get_services()
get_pvcs()
```

### Example Questions

```text
Why is my pod restarting?
Which deployment changed recently?
Are nodes healthy?
```

---

# 3. Metrics Agent

### Responsibilities

- Prometheus querying
- Resource analysis
- Capacity investigation
- Alert correlation

### Metrics

- CPU
- Memory
- Disk
- Network
- Request rate
- Error rate
- Latency

### Available Tools

```python
query_prometheus()
query_alertmanager()
```

---

# 4. Logs Agent

### Responsibilities

- Log retrieval
- Error detection
- Exception analysis
- Pattern recognition

### Data Source

- Grafana Loki

### Available Tools

```python
query_logs()
search_errors()
find_exceptions()
```

---

# 5. Tracing Agent

### Responsibilities

- Distributed trace analysis
- Service dependency mapping
- Latency bottleneck detection

### Data Source

- Grafana Tempo

### Available Tools

```python
query_traces()
find_failed_spans()
identify_bottlenecks()
```

---

# 6. Deployment Agent

### Responsibilities

- Deployment history
- CI/CD correlation
- Change analysis

### Integrations

- Jenkins
- GitHub Actions
- ArgoCD

### Available Tools

```python
get_deployment_history()
get_recent_commits()
get_pipeline_status()
```

---

# 7. Root Cause Analysis Agent

## Responsibilities

- Evidence correlation
- Incident analysis
- Root cause identification
- Confidence estimation

### Inputs

- Logs
- Metrics
- Events
- Traces
- Deployments

### Output

```json
{
  "root_cause": "...",
  "confidence": 0.92,
  "evidence": [],
  "recommendations": []
}
```

---

# 8. Remediation Agent

## Responsibilities

- Generate fixes
- Generate rollback commands
- Create remediation plans
- Execute approved actions

### Examples

```text
kubectl rollout undo deployment payment-service

kubectl scale deployment payment-service --replicas=5

helm rollback payment-service 3
```

---

# MCP Architecture

KubePilot AI will expose all infrastructure systems through Model Context Protocol (MCP) servers.

---

## Kubernetes MCP Server

### Tools

```text
list_pods
describe_pod
get_events
get_nodes
get_deployments
get_services
```

---

## Prometheus MCP Server

### Tools

```text
query_metrics
query_alerts
get_resource_usage
```

---

## Loki MCP Server

### Tools

```text
query_logs
search_errors
search_exceptions
```

---

## Tempo MCP Server

### Tools

```text
query_traces
get_trace
find_failures
```

---

## Jenkins MCP Server

### Tools

```text
deployment_history
build_status
pipeline_logs
```

---

# Memory Architecture

## Short-Term Memory

Stores:

- Current investigation
- Agent state
- Workflow progress

Technology:

- LangGraph Checkpointing

---

## Long-Term Memory

Stores:

- Previous incidents
- Root causes
- Remediation history
- Cluster knowledge

Technology:

- PostgreSQL
- pgvector

---

# Incident Timeline Generator

Example:

```text
10:02 Deployment Started

10:04 CPU Increased

10:05 Memory Spike

10:06 OOMKilled

10:07 Pod Restart

10:08 Alert Triggered

10:10 Incident Created
```

---

# AgentOps Features

## Monitoring

- Agent traces
- Token consumption
- Tool calls
- Workflow execution
- Cost analysis

## Integrations

- LangSmith
- OpenTelemetry
- Grafana
- Phoenix

---

# Security Features

## Access Control

- RBAC
- Namespace restrictions
- Read-only mode

## Agent Safety

- Human approval workflows
- Command validation
- Execution policies
- Audit logs

---

# User Interfaces

## Web Dashboard

Technology:

- Next.js
- TailwindCSS
- ShadCN

Features:

- Incident dashboard
- Agent traces
- RCA reports
- Timeline visualization

---

## CLI

```bash
kubepilot investigate payment-service

kubepilot pod analyze user-service

kubepilot incident explain INC-101
```

---

## Slack Bot

```text
@kubepilot
why is payment-service failing?
```

---

# Tech Stack

## Agent Framework

- LangGraph
- LangChain
- MCP

## Backend

- Python
- FastAPI

## Frontend

- Next.js
- TypeScript

## Database

- PostgreSQL
- pgvector
- Redis

## Kubernetes

- EKS
- Kubernetes API

## Observability

- Grafana
- Loki
- Tempo
- Prometheus

## Evaluation

- LangSmith
- DeepEval

## Deployment

- Docker
- Helm
- Kubernetes

---

# Locked Product Decisions

These decisions are locked for the initial build. They constrain scope and prevent drift.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **MVP action posture** | Read-only investigator | No writes to the cluster in Phase 1. Build trust before granting power. Remediation execution lands in Phase 4 behind HITL approvals. |
| **Distribution model** | Self-hosted OSS via Helm | Single Helm chart, deployed into the user's own cluster. All data and LLM calls stay in their environment. Matches Apache 2.0 OSS positioning. No SaaS in initial scope. |
| **Observability stack (Phase 1)** | Grafana LGTM only — Prometheus, Loki, Tempo | Fastest MVP, covers the largest cloud-native segment. Datadog / New Relic / ELK adapters are explicitly out of scope until Phase 2+. |
| **LLM strategy** | BYOK multi-provider + local models | Users plug in their own Anthropic / OpenAI / Bedrock / Azure key. Local model support (Ollama, vLLM) enables fully air-gapped deployments for regulated industries. No managed LLM costs on the project. |
| **Workload coverage** | Any containerized workload | See [Workload Scope](#workload-scope). Not specific to any language, runtime, or framework. |

---

# Development Phases

## Phase 1 (MVP)

### Features

- Kubernetes Agent (read-only)
- Metrics Agent (Prometheus queries)
- Logs Agent (Loki queries)
- RCA Agent (correlation + confidence scoring)
- Basic Web UI for triggering investigations and viewing reports
- Helm chart for self-hosted install
- BYOK config for Anthropic / OpenAI / Bedrock / Azure / Ollama / vLLM

### Explicitly Out of Scope for Phase 1

- Any write operations to the cluster (no `kubectl apply`, no rollbacks, no scaling)
- Remediation execution
- Datadog / New Relic / ELK / Splunk integrations
- Slack bot, CLI
- Managed SaaS offering
- Multi-cluster federation

### Deliverable

Autonomous, read-only incident investigation for any workload on a single Kubernetes cluster.

---

## Phase 2

### Features

- MCP Support
- Memory System
- Incident Timeline
- Slack Integration

### Deliverable

Production-ready incident analysis.

---

## Phase 3

### Features

- Multi-Agent Collaboration
- Evaluation Framework
- Knowledge Graph
- Advanced RCA

### Deliverable

Enterprise-grade Agentic SRE platform.

---

## Phase 4

### Features

- Autonomous Remediation
- Human Approval Workflow
- Auto Rollbacks
- Self-Healing Actions

### Deliverable

Autonomous Kubernetes Operations Platform.

---

# Open Source Goals

- Become the Kubernetes equivalent of GitHub Copilot for SREs
- Reduce incident investigation time by 80%
- Democratize SRE knowledge
- Enable autonomous infrastructure operations

---

# Future Vision

```text
Today:
Engineers investigate incidents manually.

Tomorrow:
Agents investigate incidents and recommend actions.

Future:
Agents investigate, fix, validate, and learn autonomously.
```

---

# Success Metrics

| Metric | Target |
|----------|---------|
| RCA Accuracy | >90% |
| MTTR Reduction | 60% |
| Incident Investigation Time | <5 Minutes |
| Agent Confidence Accuracy | >85% |
| Automated Remediation Coverage | 50% |

---

# License

Apache 2.0

---

# Author

Akash Kumar Sahani

Agentic AI Engineer | AI Infrastructure Engineer | Kubernetes & AgentOps Enthusiast
