#!/usr/bin/env bash
# Deploy the demo. Usage: ./deploy.sh [kube-context]
# Needs Solo Enterprise for Istio (ambient) and Solo Enterprise for agentgateway (istio.autoEnabled=true).
set -euo pipefail
cd "$(dirname "$0")"
K="kubectl --context ${1:-$(kubectl config current-context)}"

$K apply -f - <<'NS'
apiVersion: v1
kind: Namespace
metadata: { name: mcp-tool-modes, labels: { istio.io/dataplane-mode: ambient } }
NS
$K -n mcp-tool-modes create configmap trading-desk-server --from-file=mcp-server/server.py --dry-run=client -o yaml | $K apply -f -
$K -n mcp-tool-modes create configmap agent-proxy --from-file=default.conf=waypoint/agent-proxy.conf --dry-run=client -o yaml | $K apply -f -
$K apply -f waypoint/tool-modes-waypoint.yaml
$K -n mcp-tool-modes wait --for=condition=Programmed gateway/agw-waypoint --timeout=180s
for d in trading-desk-mcp agent rogue; do $K -n mcp-tool-modes rollout status deploy/$d --timeout=300s; done

cat <<MSG

Deployed. Reach the in-mesh agent from your laptop:
  kubectl -n mcp-tool-modes port-forward deploy/agent 18940:8080
Then:
  uv run harness/probe.py                              # what each tool mode advertises
  ANTHROPIC_API_KEY=... uv run harness/measure.py      # token spend per tool mode
  ./harness/check-waypoint.sh                          # proves traffic goes through the waypoint
MSG
