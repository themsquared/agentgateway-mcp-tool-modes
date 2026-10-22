#!/usr/bin/env bash
# Proves the MCP traffic really goes through the agentgateway waypoint, and that the policies hold.
# Usage: ./harness/check-waypoint.sh [kube-context]
set -uo pipefail
K="kubectl --context ${1:-$(kubectl config current-context)} -n mcp-tool-modes"
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'

echo "== 1. the four MCP Services are bound to the waypoint (ztunnel view)"
istioctl ${1:+--context $1} ztunnel-config service 2>/dev/null | awk 'NR==1 || /mcp-tool-modes/ && /mcp-/' || echo "   (istioctl not found; skipping)"

echo; echo "== 2. the 'agent' workload is allowed (SPIFFE identity sa/agent)"
$K exec deploy/agent -- wget -q -O - --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' --post-data "$INIT" \
  http://mcp-codesearch.mcp-tool-modes/mcp 2>&1 | grep -o '"serverInfo":{[^}]*}' || echo "   FAILED"

echo; echo "== 3. a different in-mesh workload (sa/rogue) is denied by the waypoint policy"
$K exec deploy/rogue -- curl -s -o /dev/null -w "   HTTP %{http_code}\n" -X POST http://mcp-codesearch.mcp-tool-modes/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT"

echo; echo "== 4. skipping the waypoint (straight to the MCP server pod) is blocked by ztunnel"
$K exec deploy/rogue -- curl -s -m 5 -o /dev/null -w "   HTTP %{http_code}\n" -X POST http://trading-desk-mcp.mcp-tool-modes:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT" || echo "   connection rejected (curl exit $?)"

echo; echo "== 5. the waypoint's access log: MCP-aware, with the caller's mesh identity"
$K logs deploy/agw-waypoint --tail=50 | grep 'protocol=mcp' | tail -3 \
  | grep -o -E 'route=[^ ]+|src.identity=[^ ]+|http.status=[0-9]+|mcp.method.name=[^ ]+' | paste -sd ' ' - | fold -w 160
