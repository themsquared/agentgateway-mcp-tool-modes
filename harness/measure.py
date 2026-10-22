# /// script
# dependencies = ["anthropic", "mcp==1.30.0"]
# ///
"""Run the same agent task through each agentgateway route and measure what it cost.

For every route we connect an MCP client to the gateway, hand the tools it advertises
to Claude, and run a normal tool-use loop until Claude answers. We record:

  startup_tokens   tokens in the very first request (tool definitions + prompt), via count_tokens
  turns            model round trips
  tool_calls       tool calls the model made
  result_kb        tool-result payload the model had to read (KB)
  input_tokens     sum of input tokens over every turn (history is resent each turn)
  output_tokens    sum of output tokens (includes thinking)
  cost_usd         input/output tokens x list price
  seconds          wall clock
  correct          answer contains the 5 right accounts and the right total

Usage:  uv run harness/measure.py [--base http://127.0.0.1:18940] [--runs 3] [--model claude-opus-5-5]
"""
import argparse, asyncio, json, re, sys, time
from pathlib import Path

import anthropic
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp-server"))
from server import expected_top  # same seed as the server -> ground truth

ROUTES = ["standard", "search", "code", "codesearch"]
PRICES = {  # USD per 1M tokens (input, output), Anthropic list price
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
SYSTEM = "You are a trading-operations analyst. Use the available tools to get data. Never guess numbers."
TASK = (
    "Find the 5 accounts with the largest absolute net notional exposure in the LCX futures product. "
    "Notional = |net_qty| x mark_price x the product's contract multiplier, summed per account. "
    "For each account give account_id, firm_name and notional in USD rounded to the nearest dollar, "
    "then the combined total of those 5. Finish with the answer as a JSON object."
)


def to_text(result) -> str:
    parts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    if parts:
        return "\n".join(parts)
    if result.structuredContent is not None:
        return json.dumps(result.structuredContent)
    return ""


def check(answer: str):
    rows, total = expected_top()
    ids_found = sum(1 for r in rows if r["account_id"] in answer)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]{6,}(?:\.\d+)?", answer)]
    total_ok = any(abs(n - total) / total < 0.005 for n in nums)
    return ids_found == 5 and total_ok, ids_found, total_ok


async def run_route(client, base, route, model, max_turns, effort):
    t0 = time.time()
    async with streamablehttp_client(f"{base}/mcp/{route}", timeout=60, sse_read_timeout=300) as (r, w, _):
        async with ClientSession(r, w) as mcp:
            await mcp.initialize()
            mcp_tools = (await mcp.list_tools()).tools
            tools = [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema} for t in mcp_tools]
            messages = [{"role": "user", "content": TASK}]
            extra = {"output_config": {"effort": effort}} if effort else {}

            startup = client.messages.count_tokens(model=model, system=SYSTEM, tools=tools, messages=messages).input_tokens
            stats = dict(route=route, tools_advertised=len(tools), startup_tokens=startup, turns=0, tool_calls=0,
                         result_bytes=0, largest_result_bytes=0, input_tokens=0, output_tokens=0, calls=[], per_turn=[])
            answer = ""
            for _ in range(max_turns):
                resp = client.messages.create(model=model, max_tokens=16000, system=SYSTEM, tools=tools,
                                              messages=messages, **extra)
                stats["turns"] += 1
                u = resp.usage
                stats["input_tokens"] += u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
                stats["output_tokens"] += u.output_tokens
                stats["per_turn"].append({"turn": stats["turns"], "input_tokens": u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0),
                                          "tools_called": [b.name if b.name not in ("invoke_tool", "get_tool") else f"{b.name}({b.input.get('name', '')})" for b in resp.content if b.type == "tool_use"]})
                messages.append({"role": "assistant", "content": resp.content})
                uses = [b for b in resp.content if b.type == "tool_use"]
                if resp.stop_reason != "tool_use" or not uses:
                    answer = "\n".join(b.text for b in resp.content if b.type == "text")
                    stats["stop_reason"] = resp.stop_reason
                    break
                results = []
                for b in uses:
                    try:
                        res = await mcp.call_tool(b.name, b.input)
                        text, is_err = to_text(res), bool(res.isError)
                    except Exception as e:  # surface gateway/sandbox errors to the model, like a real client
                        text, is_err = f"tool error: {e}", True
                    n = len(text.encode())
                    stats["tool_calls"] += 1
                    stats["result_bytes"] += n
                    stats["largest_result_bytes"] = max(stats["largest_result_bytes"], n)
                    stats["calls"].append({"tool": b.name, "result_bytes": n, "error": is_err,
                                           "input": json.dumps(b.input)[:200]})
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": text or "(empty)", "is_error": is_err})
                messages.append({"role": "user", "content": results})
            else:
                stats["stop_reason"] = "max_turns"

    pin, pout = PRICES.get(model, (0, 0))
    ok, ids, total_ok = check(answer)
    stats.update(seconds=round(time.time() - t0, 1), correct=ok, ids_found=ids, total_ok=total_ok,
                 cost_usd=round(stats["input_tokens"] / 1e6 * pin + stats["output_tokens"] / 1e6 * pout, 4),
                 answer=answer[-1500:])
    return stats


def table(rows):
    base = next((r for r in rows if r["route"] == "standard"), None)
    hdr = f"{'route':11s} {'tools':>5s} {'startup tok':>11s} {'turns':>5s} {'calls':>5s} {'result KB':>9s} {'input tok':>10s} {'output tok':>10s} {'total tok':>10s} {'vs std':>7s} {'cost $':>7s} {'secs':>5s}  correct"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        tot = r["input_tokens"] + r["output_tokens"]
        vs = f"{(tot / (base['input_tokens'] + base['output_tokens']) - 1) * 100:+.0f}%" if base and r is not base else "base"
        print(f"{r['route']:11s} {r['tools_advertised']:5d} {r['startup_tokens']:11,d} {r['turns']:5d} {r['tool_calls']:5d} "
              f"{r['result_bytes'] / 1024:9.1f} {r['input_tokens']:10,d} {r['output_tokens']:10,d} {tot:10,d} {vs:>7s} "
              f"{r['cost_usd']:7.3f} {r['seconds']:5.0f}  {'YES' if r['correct'] else 'no (%d/5 ids, total %s)' % (r['ids_found'], r['total_ok'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18940")
    ap.add_argument("--routes", default=",".join(ROUTES))
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="", help="low|medium|high (blank = model default)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--max-turns", type=int, default=15)
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--per-turn", action="store_true", help="print input tokens for every turn")
    a = ap.parse_args()

    client = anthropic.Anthropic()
    rows, all_runs = [], []
    for route in a.routes.split(","):
        runs = []
        for i in range(a.runs):
            print(f"> {route} run {i + 1}/{a.runs} ...", flush=True)
            s = asyncio.run(run_route(client, a.base, route, a.model, a.max_turns, a.effort))
            print(f"  {s['turns']} turns, {s['tool_calls']} calls, {s['input_tokens'] + s['output_tokens']:,} tokens, "
                  f"${s['cost_usd']:.3f}, {s['seconds']}s, correct={s['correct']}  calls={[c['tool'] for c in s['calls']]}", flush=True)
            runs.append(s)
        all_runs += runs
        # report the median run by total tokens
        runs.sort(key=lambda s: s["input_tokens"] + s["output_tokens"])
        rows.append(runs[len(runs) // 2])
    print(f"\nModel {a.model}, task: top-5 LCX notional exposure (median of {a.runs} run(s) per route)\n")
    table(rows)
    Path(a.out).write_text(json.dumps({"model": a.model, "task": TASK, "summary": rows, "runs": all_runs}, indent=2, default=str))
    if a.per_turn:
        for r in rows:
            print(f"\n{r['route']}: input tokens per turn (each turn re-reads everything before it)")
            for t in r["per_turn"]:
                print(f"  turn {t['turn']}: {t['input_tokens']:8,d}  -> {', '.join(t['tools_called']) or 'final answer'}")
    print(f"\nfull detail (every tool call, final answers) -> {a.out}")


if __name__ == "__main__":
    main()
