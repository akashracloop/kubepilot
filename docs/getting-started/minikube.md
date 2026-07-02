# Run KubePilot AI on minikube

A one-command local demo of the whole platform on a laptop — real Kubernetes,
real Prometheus + Loki, real investigations powered by **OpenAI `gpt-4o-mini`**.
Great for trying KubePilot, recording a demo, or validating the Helm chart before
a cluster install.

> For a production / general Helm install, see [install.md](./install.md).
> For provider options (Anthropic, Bedrock, Ollama, …) see
> [../configuration/llm-providers.md](../configuration/llm-providers.md).

---

## Prerequisites

| Tool | Install |
|---|---|
| minikube | `brew install minikube` |
| kubectl | `brew install kubectl` |
| helm | `brew install helm` |
| Docker | Docker Desktop (minikube uses the `docker` driver) |
| uv (for the CLI) | `brew install uv` |

Resources: the Phase-2 stack wants **~4 CPU / 8 GB** for minikube.

You also need an **OpenAI API key** (`sk-...`). The key is loaded into a Kubernetes
Secret from your shell environment — it is **never written into any file** in the
repo.

---

## Quickstart (one command)

```bash
export OPENAI_API_KEY=sk-...        # your key
make minikube-up                    # or: bash scripts/minikube-up.sh
```

That script does everything:

1. `minikube start --cpus=4 --memory=8192`
2. **Builds the service images into minikube's own Docker daemon** (`eval $(minikube docker-env)`), so there is **no registry push/pull** — the pods run your locally-built images via `imagePullPolicy: IfNotPresent`.
3. Installs **Prometheus + Loki** into the `observability` namespace.
4. Creates the `kubepilot-llm-credentials` Secret from `$OPENAI_API_KEY`.
5. `helm install kubepilot-ai` with [`values-local.yaml`](../../charts/kubepilot-ai/values-local.yaml) — `gpt-4o-mini` for every role, bundled ephemeral Postgres (pgvector) + Redis, long-term memory on.
6. Deploys three **sample failing workloads** into the `demo` namespace: `oom-app` (OOMKilled), `crash-app` (CrashLoopBackOff), `imagepull-app` (ImagePullBackOff).

Give the platform pods a minute to become Ready:

```bash
kubectl -n kubepilot-system get pods -w
```

---

## Run an investigation

### Web UI

```bash
kubectl -n kubepilot-system port-forward svc/kubepilot-ai-web-ui 3000:3000
open http://localhost:3000   # auth is disabled in values-local — no key needed                 # API key: local-dev-key
```

Auth is disabled in the local profile (laptop-only), so no key is needed. Trigger an investigation for `oom-app` in namespace `demo`. The report shows the
root cause + confidence, evidence, the **timeline**, any **similar past
incidents** (memory), and recommendations.

### CLI

```bash
kubectl -n kubepilot-system port-forward svc/kubepilot-ai-api-gateway 8080:8080 &
export KUBEPILOT_API_URL=http://localhost:8080 KUBEPILOT_API_KEY=local-dev-key

uv run kubepilot investigate oom-app       -n demo --wait
uv run kubepilot investigate crash-app     -n demo --wait
uv run kubepilot investigate imagepull-app -n demo --wait
```

See [../features/cli.md](../features/cli.md) for all CLI options (`--output json`, etc.).

---

## What runs (and what doesn't) by default

The base local profile enables **Kubernetes + Metrics + Logs + Memory**. It leaves
**Tracing (Tempo)** and **Deployment (CI)** off, because a laptop demo has no trace
source or CI backend — the Tracing/Deployment branches simply don't run (not an
error). To turn them on:

```bash
# Tempo (needs a workload emitting traces + a Tempo install in-cluster)
helm upgrade kubepilot-ai charts/kubepilot-ai -n kubepilot-system \
  -f charts/kubepilot-ai/values-local.yaml \
  --set mcp.tempo.enabled=true \
  --set mcp.tempo.upstream.url=http://tempo.observability.svc.cluster.local:3200

# CI/Deployment (needs a GitHub Actions / Jenkins / ArgoCD token)
#   see ../features/tracing-and-ci.md
```

Long-term **memory** is on: the first time you investigate an incident it's
embedded (OpenAI embeddings) into pgvector; investigate a similar one again and
it shows up under "similar past incidents." Memory is opt-out via
`--set apiGateway.memory_enabled=false`.

---

## Teardown

```bash
make minikube-down            # uninstall KubePilot + observability + demo workloads
make minikube-down ARGS=--all # …and delete the whole minikube cluster
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Pods `ImagePullBackOff` for `kubepilot-*` images | You ran `helm install` in a shell where `eval $(minikube docker-env)` wasn't active, so the image isn't in minikube's daemon. Re-run `make minikube-up` (it builds into the right daemon). |
| `oom-app` etc. stuck `ContainerCreating` | The sample images (`polinux/stress`, `busybox`, `nginx`) need internet to pull the first time. |
| Investigation returns low-confidence / "Unknown" | Prometheus/Loki need a minute to scrape the new workloads; retry after pods have been running ~2 min. |
| `ProviderNotConfigured` in the gateway logs | The `kubepilot-llm-credentials` Secret is missing/empty — confirm `OPENAI_API_KEY` was exported before `make minikube-up`. |
| Out of memory / pods evicted | Give minikube more RAM: `minikube stop && minikube config set memory 10240 && make minikube-up`. |

More general issues: [troubleshooting.md](./troubleshooting.md).

> **Security:** after you're done, revoke the OpenAI key you used for the demo
> (OpenAI dashboard → API keys) — especially if it was shared anywhere.
