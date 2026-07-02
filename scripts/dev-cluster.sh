#!/usr/bin/env bash
#
# Local dev kind cluster with sample workloads.
#
# Usage:
#   scripts/dev-cluster.sh up       # create cluster, deploy sample workloads + obs stack
#   scripts/dev-cluster.sh down     # delete cluster
#   scripts/dev-cluster.sh status   # show cluster + workload status
#
# Requires: kind, kubectl, helm
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-kubepilot-dev}"

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "error: required tool '$1' not found in PATH" >&2
        echo "  install hint: $2" >&2
        exit 1
    }
}

cmd_up() {
    require kind   "brew install kind"
    require kubectl "brew install kubectl"
    require helm   "brew install helm"

    echo "==> Creating kind cluster '${CLUSTER_NAME}'"
    if ! kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
        kind create cluster --name "${CLUSTER_NAME}" --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080
        hostPort: 30080
        protocol: TCP
EOF
    else
        echo "    cluster already exists"
    fi

    echo "==> Installing Prometheus + Loki via Helm"
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
    helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
    helm repo update >/dev/null

    kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f -

    helm upgrade --install prom prometheus-community/prometheus \
        -n observability \
        --set server.persistentVolume.enabled=false \
        --set alertmanager.enabled=false \
        --wait --timeout 5m

    helm upgrade --install loki grafana/loki-stack \
        -n observability \
        --set loki.persistence.enabled=false \
        --set promtail.enabled=true \
        --set grafana.enabled=false \
        --wait --timeout 5m

    echo "==> Creating sample workloads namespace 'demo' (workloads added in W2-W4)"
    kubectl create namespace demo --dry-run=client -o yaml | kubectl apply -f -

    echo
    echo "Cluster ready. Switch context:  kubectl config use-context kind-${CLUSTER_NAME}"
    echo "Observability:                   kubectl -n observability get pods"
}

cmd_down() {
    require kind "brew install kind"
    echo "==> Deleting kind cluster '${CLUSTER_NAME}'"
    kind delete cluster --name "${CLUSTER_NAME}"
}

cmd_status() {
    require kubectl "brew install kubectl"
    kubectl --context "kind-${CLUSTER_NAME}" get nodes
    echo
    kubectl --context "kind-${CLUSTER_NAME}" get pods -A
}

case "${1:-}" in
    up)     cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    *)
        echo "usage: $0 {up|down|status}" >&2
        exit 2
        ;;
esac
