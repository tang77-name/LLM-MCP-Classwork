"""In-process test: connect Client to MCPServer, list tools and call ask_workspace."""

import asyncio
import sys

sys.path.insert(0, r"E:\anythingLLMMCP")
import server
from mcp import Client


async def main():
    async with Client(server.mcp) as client:
        listed = await client.list_tools()
        names = [t.name for t in listed.tools]
        print("tools:", names)
        assert "ask_workspace" in names, "tool missing"

        print("calling ask_workspace ...")
        res = await client.call_tool("ask_workspace", {"message": "用一句话介绍你自己"})
        print("result_type:", res.result_type)
        print("content:", res.content)


asyncio.run(main())
