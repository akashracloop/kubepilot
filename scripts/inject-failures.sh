#!/usr/bin/env bash
# inject-failures.sh — apply a canned failure workload for a live KubePilot demo.
#
#   ./scripts/inject-failures.sh <type> [name] [namespace]
#
#   type       one of: oom | crashloop | imagepull   (or `all`)
#   name       Deployment name to render (default: the manifest's own name)
#   namespace  target namespace (default: demo)
#
# Examples:
#   ./scripts/inject-failures.sh oom payment-service
#   ./scripts/inject-failures.sh all
#   ./scripts/inject-failures.sh crashloop billing-svc staging
#
# Cleanup:
#   ./scripts/inject-failures.sh clean            # delete everything in the ns
#
# The manifests live in scripts/demo-workloads/. This wrapper substitutes the
# Deployment/label name and namespace, then `kubectl apply`s. Read-only KubePilot
# then investigates the resulting incident.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKLOADS="${SCRIPT_DIR}/demo-workloads"
TYPES=(oom crashloop imagepull)

TYPE="${1:-}"
NAME="${2:-}"
NAMESPACE="${3:-demo}"

usage() {
  echo "usage: $0 <oom|crashloop|imagepull|all|clean> [name] [namespace]" >&2
  exit 2
}

[[ -z "${TYPE}" ]] && usage
command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found on PATH" >&2; exit 1; }

ensure_ns() {
  kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1 || kubectl create namespace "${NAMESPACE}"
}

apply_one() {
  local t="$1"
  local manifest="${WORKLOADS}/${t}.yaml"
  [[ -f "${manifest}" ]] || { echo "no manifest for type '${t}' at ${manifest}" >&2; exit 1; }

  local default_name="${t}-app"
  local target_name="${NAME:-$default_name}"

  # Render: rename the Deployment + its app label, and set the namespace.
  # Plain string replace (no \b — BSD/macOS sed lacks it); the default names are
  # specific enough that a global swap is safe.
  sed -e "s/${default_name}/${target_name}/g" \
      -e "s/^  namespace: .*/  namespace: ${NAMESPACE}/" \
      "${manifest}" | kubectl apply -f -
  echo "injected '${t}' as ${target_name} in namespace ${NAMESPACE}"
}

case "${TYPE}" in
  clean)
    kubectl delete namespace "${NAMESPACE}" --ignore-not-found
    ;;
  all)
    ensure_ns
    for t in "${TYPES[@]}"; do NAME="" apply_one "${t}"; done
    ;;
  oom|crashloop|imagepull)
    ensure_ns
    apply_one "${TYPE}"
    ;;
  *)
    usage
    ;;
esac
