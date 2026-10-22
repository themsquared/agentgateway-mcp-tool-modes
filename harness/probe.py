# /// script
# dependencies = ["mcp==1.30.0"]
# ///
"""Show what a client sees on each route: tool names and tools/list size."""
import asyncio, json, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18940"
ROUTES = ["standard", "search", "code", "codesearch"]


async def probe(route):
    async with streamablehttp_client(f"{BASE}/mcp/{route}") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            size = len(json.dumps([t.model_dump(exclude_none=True) for t in tools]))
            names = [t.name for t in tools]
            print(f"{route:11s} {len(tools):3d} tools  {size:7,d} bytes  {names[:6]}{' ...' if len(names) > 6 else ''}")
            if "--schemas" in sys.argv and route != "standard":
                for t in tools:
                    print("   ", t.name, json.dumps(t.inputSchema)[:300])
                    print("     desc:", (t.description or "")[:500].replace("\n", " | "))


async def main():
    for r in ROUTES:
        await probe(r)

asyncio.run(main())
