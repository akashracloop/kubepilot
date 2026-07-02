#!/usr/bin/env bash
# Tear down the local KubePilot AI demo.
#   bash scripts/minikube-down.sh          # uninstall releases + workloads
#   bash scripts/minikube-down.sh --all    # also delete the whole minikube cluster
set -euo pipefail

NS="kubepilot-system"

if [[ "${1:-}" == "--all" ]]; then
  echo "==> Deleting the minikube cluster entirely"
  minikube delete
  exit 0
fi

echo "==> Uninstalling KubePilot AI + observability + demo workloads"
helm uninstall kubepilot-ai -n "$NS" 2>/dev/null || true
helm uninstall prom -n observability 2>/dev/null || true
helm uninstall loki -n observability 2>/dev/null || true
kubectl delete namespace demo --ignore-not-found
kubectl delete namespace "$NS" --ignore-not-found
kubectl delete namespace observability --ignore-not-found
echo "Done. (Cluster still running — 'bash scripts/minikube-down.sh --all' to delete it.)"
