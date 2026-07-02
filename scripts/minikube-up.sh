#!/usr/bin/env bash
#
# One-command local demo of KubePilot AI on minikube, using OpenAI gpt-4o-mini.
#
#   export OPENAI_API_KEY=sk-...
#   bash scripts/minikube-up.sh
#
# Builds images INTO minikube's docker daemon (no registry push), installs
# Prometheus + Loki, installs KubePilot (values-local.yaml), and deploys sample
# failing workloads into the `demo` namespace. Tear down with scripts/minikube-down.sh.
set -euo pipefail

REG="ghcr.io/akashsahani2001"
TAG="0.1.0-dev"
NS="kubepilot-system"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

require() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found. $2"; exit 1; }; }
require minikube "brew install minikube"
require kubectl  "brew install kubectl"
require helm     "brew install helm"
require docker   "install Docker Desktop"
: "${OPENAI_API_KEY:?Set it first:  export OPENAI_API_KEY=sk-...}"

echo "==> Starting minikube (4 CPU / 8GB)"
minikube start --cpus=4 --memory=8192 --driver=docker

echo "==> Building images into minikube's docker daemon (no push needed)"
eval "$(minikube docker-env)"
# service dir -> chart image repository
build() { echo "  - $2"; docker build -q -f "services/$1/Dockerfile" -t "$REG/$2:$TAG" . >/dev/null; }
build api-gateway kubepilot-api
build mcp-k8s     kubepilot-mcp-k8s
build mcp-prom    kubepilot-mcp-prom
build mcp-loki    kubepilot-mcp-loki
echo "  - kubepilot-web-ui"
docker build -q -t "$REG/kubepilot-web-ui:$TAG" services/web-ui >/dev/null
# (mcp-tempo / mcp-ci / slack are disabled in values-local; build them only if you enable those.)

echo "==> Installing observability (Prometheus + Loki) into namespace 'observability'"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null
kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install prom prometheus-community/prometheus -n observability \
  --set alertmanager.enabled=false --set prometheus-pushgateway.enabled=false --wait --timeout 5m
helm upgrade --install loki grafana/loki-stack -n observability \
  --set loki.persistence.enabled=false --wait --timeout 5m

echo "==> Creating namespace + LLM credentials Secret (from \$OPENAI_API_KEY)"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic kubepilot-llm-credentials \
  --from-literal=openai_api_key="$OPENAI_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Installing KubePilot AI (values-local.yaml)"
helm upgrade --install kubepilot-ai charts/kubepilot-ai -n "$NS" \
  -f charts/kubepilot-ai/values-local.yaml --wait --timeout 8m

echo "==> Deploying sample failing workloads into namespace 'demo'"
kubectl create namespace demo --dry-run=client -o yaml | kubectl apply -f -
kubectl -n demo apply -f scripts/demo-workloads/

cat <<EOF

==================================================================
KubePilot AI is up on minikube. Next steps:

  # Watch the platform come ready
  kubectl -n $NS get pods -w

  # Web UI  → http://localhost:3000   (API key: local-dev-key)
  kubectl -n $NS port-forward svc/kubepilot-ai-web-ui 3000:3000

  # API / CLI
  kubectl -n $NS port-forward svc/kubepilot-ai-api-gateway 8080:8080
  export KUBEPILOT_API_URL=http://localhost:8080 KUBEPILOT_API_KEY=local-dev-key
  uv run kubepilot investigate oom-app -n demo --wait
  uv run kubepilot investigate crash-app -n demo --wait
  uv run kubepilot investigate imagepull-app -n demo --wait

Tear down:  bash scripts/minikube-down.sh
==================================================================
EOF
