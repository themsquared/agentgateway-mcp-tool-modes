# agentgateway MCP tool modes, measured through an ambient waypoint

One deliberately heavy MCP server, fronted by **agentgateway running as an Istio ambient waypoint**, exposed four ways. The only difference between the four is one field, `entMcp.toolMode`. A real Claude agent runs the same task through each mode, and the harness counts every token it spends.

| Mode | What the agent sees | Turns | Tool results the model read | **Total tokens** | vs Standard | Cost |
|---|---|---:|---:|---:|---:|---:|
| `Standard` | all 36 tools (passthrough) | 3 | 184.3 KB | **222,834** | baseline | $0.92 |
| `Search` | `get_tool` + `invoke_tool` | 5 | 185.5 KB | **285,600** | **+28%** | $1.18 |
| `Code` | `run_code` (typed JS API for all tools) | 5 | 3.2 KB | **51,475** | **−77%** | $0.25 |
| `CodeSearch` | `get_tool` + `run_code` | 6 | 3.3 KB | **22,325** | **−90%** | **$0.14** |

Median of 3 runs per mode, `claude-opus-5-5`, all 12 runs answered correctly. Solo Enterprise for agentgateway v2026.9.3 as a waypoint on Solo Enterprise for Istio 1.31.1 (ambient). Full per-call detail: [`results/results.json`](results/results.json).

## What the numbers say

**Large tool responses cost more than a large tool list.** Standard spends 12K tokens on 36 tool definitions, but the 186 KB `get_positions` response (~91K tokens) lands in the conversation and is paid for again on every later turn:

```
standard: input tokens per turn (each turn re-reads everything before it)
  turn 1:   12,483  -> get_product_spec, get_positions
  turn 2:  103,439  -> get_account x5
  turn 3:  105,078  -> final answer
```

**Search alone made it worse (+28%).** It shrinks the tool list to two meta tools, but it doesn't touch responses, and its lookup steps add turns. Each extra turn re-reads the same 91K-token payload:

```
search:
  turn 1:    1,062  -> get_tool(get_positions), get_tool(get_product_spec)
  turn 2:    1,541  -> invoke_tool(get_product_spec), invoke_tool(get_positions)
  turn 3:   92,569  -> get_tool(get_account)        <- discovery step, payload still in context
  turn 4:   93,420  -> invoke_tool(get_account) x5
  turn 5:   94,697  -> final answer
```

**Code mode keeps the payload inside the gateway.** The model writes a script, agentgateway runs it in its sandbox against the upstream tools, and only the 5-row answer comes back. **CodeSearch** also drops the 8K-token typed API from the first request, so it wins on both:

```
codesearch:
  turn 1:    1,464  -> get_tool x3
  turn 2-5:  2,088 .. 4,269  -> run_code
  turn 6:    5,010  -> final answer
```

**CodeSearch was the most consistent.** In one of three Code runs the model's first script returned raw rows and that run cost 338K tokens. All three CodeSearch runs stayed between 18.8K and 31.2K.

## Architecture

```
agent pod (ambient, sa/agent)
   │  http://mcp-<mode>.mcp-tool-modes/mcp
   ▼
ztunnel ──HBONE──▶ agw-waypoint (enterprise-agentgateway-waypoint)
                     ├─ EnterpriseAgentgatewayPolicy: only sa/agent may call (SPIFFE, from mTLS)
                     ├─ HTTPRoute (parentRef: Service mcp-<mode>) ──▶ EnterpriseAgentgatewayBackend toolMode: <mode>
                     ▼
                   ──HBONE──▶ trading-desk-mcp (AuthorizationPolicy: only the waypoint's identity)
```

- `mcp-standard`, `mcp-search`, `mcp-code`, `mcp-codesearch` are Services with no pods, labelled `istio.io/use-waypoint: agw-waypoint`. The waypoint owns those hostnames.
- The `agent` pod is an nginx proxy standing in for an agent runtime. Its calls leave through ztunnel like any in-mesh agent's would. The harness reaches it with a port-forward.
- `rogue` is a second in-mesh identity used to prove the waypoint policy denies it.

## Gotcha: use `static.backendRef`, not `static.host`, for MCP targets behind a waypoint

With `static.host`, the waypoint's connection to the MCP server arrives **without a mesh identity**. An `AuthorizationPolicy` that only allows the waypoint's principal then rejects it, and the agent sees:

```
{"jsonrpc":"2.0","id":1,"error":{"code":-32603,"message":"failed to send message: http upstream error: http request failed: upstream call failed: SendRequest: connection closed before message completed"}}
```

ztunnel logs the reason, with no `src.identity` on the connection:

```
error="connection closed due to policy rejection: allow policies exist, but none allowed"
```

Point the target at the Service instead, and the waypoint connects over HBONE as `spiffe://cluster.local/ns/mcp-tool-modes/sa/agw-waypoint`:

```yaml
targets:
  - name: desk
    static: { backendRef: { name: trading-desk-mcp }, port: 8000, path: /mcp, protocol: StreamableHTTP }
```

## Run it

Prerequisites:
- A Kubernetes cluster with **Solo Enterprise for Istio** in ambient mode ([install](https://docs.solo.io/istio/latest/setup/install/)).
- **Solo Enterprise for agentgateway** installed with `--set istio.autoEnabled=true` ([waypoint install](https://docs.solo.io/agentgateway/latest/integrations/agentic-mesh/waypoint/install/)). Both need Enterprise licenses.
- `kubectl`, [`uv`](https://docs.astral.sh/uv/), and `istioctl` (optional, for the checks). An Anthropic API key for the measurement.

```bash
./deploy.sh <kube-context>
```

```bash
kubectl -n mcp-tool-modes port-forward deploy/agent 18940:8080
```

```bash
./harness/check-waypoint.sh <kube-context>
```

```bash
uv run harness/probe.py
```

```bash
ANTHROPIC_API_KEY=... uv run harness/measure.py --runs 3 --per-turn
```

One run per mode takes about 2 minutes and costs about $2.50 on Opus 5.5. Add `--model claude-haiku-5-5` for a cheap rehearsal.

`check-waypoint.sh` output from the measured run:

```
== 3. a different in-mesh workload (sa/rogue) is denied by the waypoint policy
   HTTP 403
== 4. skipping the waypoint (straight to the MCP server pod) is blocked by ztunnel
   connection rejected (curl exit 56)
== 5. the waypoint's access log: MCP-aware, with the caller's mesh identity
route=mcp-tool-modes/mcp-codesearch src.identity=spiffe://cluster.local/ns/mcp-tool-modes/sa/agent http.status=200 mcp.method.name=initialize
```

## How the measurement works

`harness/measure.py` is a plain Claude tool-use loop (Anthropic Python SDK) with the same prompt and model for every mode:

1. Connect an MCP client to the mode's endpoint and hand Claude exactly the tools agentgateway advertises.
2. Count the startup tokens (tool definitions + prompt) with `count_tokens`.
3. Run the agent to an answer, adding `usage.input_tokens` and `usage.output_tokens` from every turn.
4. Grade the answer against ground truth computed from the server's fixed seed (5 account IDs and the total, within 0.5%).

No prompt caching is used. Caching lowers the dollar cost of re-reads, but not the token counts or the ranking.

**The task:** *Find the 5 accounts with the largest absolute net notional exposure in the LCX futures product, with firm names and the combined total.* That is one large data pull, one spec lookup, five account lookups and some arithmetic.

## Files

| Path | What |
|---|---|
| `mcp-server/server.py` | The heavy MCP server (FastMCP, `mcp==1.30.0`). Synthetic data, invented instruments, fixed seed. |
| `waypoint/tool-modes-waypoint.yaml` | Namespace, MCP server, waypoint, 4 backends, 4 Services + HTTPRoutes, policies, agent + rogue pods |
| `waypoint/agent-proxy.conf` | nginx config for the in-mesh agent proxy |
| `deploy.sh` | Applies everything and waits for it |
| `harness/probe.py` | Prints what each mode advertises |
| `harness/measure.py` | Runs the agent per mode and prints the token table |
| `harness/check-waypoint.sh` | Proves traffic goes through the waypoint and the policies hold |
| `results/` | Raw output and per-call JSON from the measured run |

## Docs

- [MCP tool modes](https://docs.solo.io/agentgateway/latest/mcp/tool-mode/)
- [Code mode](https://docs.solo.io/agentgateway/latest/mcp/tool-mode/code-mode/)
- [agentgateway as a waypoint proxy](https://docs.solo.io/agentgateway/latest/integrations/agentic-mesh/waypoint/install/)

All data in this repo is synthetic. The instruments, accounts and firms are invented.
