"""HTTP test: connect to the running Streamable HTTP MCP server, speak 2026-07-28."""

import json
import urllib.request

URL = "http://127.0.0.1:8765/mcp"
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "http-test", "version": "1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def call(method: str, params: dict | None = None) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    body["params"] = dict(params or {})
    body["params"]["_meta"] = META
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Method": method,
        "Mcp-Protocol-Version": "2026-07-28",
    }
    # 2026-07-28: 调用命名工具时必须带 Mcp-Name 头
    if method == "tools/call" and "name" in params:
        headers["Mcp-Name"] = params["name"]
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode("utf-8")
    # HTTP response may be plain JSON or SSE; extract the JSON object either way
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(raw)


if __name__ == "__main__":
    d = call("server/discover")
    print("server/discover:", d["result"]["supportedVersions"])

    t = call("tools/list")
    print("tools:", [x["name"] for x in t["result"]["tools"]])

    c = call("tools/call", {"name": "ask_workspace", "arguments": {"message": "1+1等于几？只回答数字"}})
    content = c["result"]["content"]
    print("tools/call ok, content len:", sum(len(x.get("text", "")) for x in content))
    print("HTTP TEST PASSED")
