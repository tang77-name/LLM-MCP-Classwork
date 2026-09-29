"""AnythingLLM MCP Server - 访问本机 AnythingLLM,基于第一个工作区回答问题。

MCP 协议: 2026-07-28 (Python SDK v2, mcp>=2.0)
传输: Streamable HTTP (无状态模式)
默认地址: http://127.0.0.1:8765/mcp
配置(环境变量,均有默认值):
  ANYLLM_BASE_URL  AnythingLLM 地址, 默认 http://localhost:3001
  ANYLLM_API_KEY    AnythingLLM API Key, 默认用户提供的 key
  MCP_HTTP_PORT     HTTP 监听端口, 默认 8765
"""

import json
import os
import urllib.error
import urllib.request

from mcp.server import MCPServer

BASE_URL = os.environ.get("ANYLLM_BASE_URL", "http://localhost:3001").rstrip("/")
API_KEY = os.environ.get("ANYLLM_API_KEY", "4HXAD7P-6Y8MF8X-QBXZD8A-E5037CM")

mcp = MCPServer("anythingllm-mcp", version="0.1.0")


def _api(path: str, body: dict | None = None) -> dict:
    """调用 AnythingLLM REST API,统一处理鉴权与错误。"""
    req = urllib.request.Request(
        BASE_URL + path, method="POST" if body is not None else "GET"
    )
    req.add_header("Authorization", f"Bearer {API_KEY}")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode("utf-8")
    try:
        with urllib.request.urlopen(req, data=data, timeout=600) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"AnythingLLM API {e.code}: {e.read().decode('utf-8')[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 AnythingLLM ({BASE_URL}): {e.reason}")


@mcp.tool()
def ask_workspace(message: str) -> str:
    """基于 AnythingLLM 第一个工作区中嵌入的知识回答用户的问题。

    Args:
        message: 用户要问的问题。
    """
    workspaces = _api("/api/v1/workspaces").get("workspaces", [])
    if not workspaces:
        raise RuntimeError("AnythingLLM 中没有任何工作区")
    slug = workspaces[0]["slug"]
    result = _api(
        f"/api/v1/workspace/{slug}/chat",
        {"message": message, "mode": "chat"},
    )
    answer = result.get("textResponse") or result.get("error") or "AnythingLLM 未返回回答"
    sources = result.get("sources") or []
    if sources:
        answer += "\n\n来源:\n" + "\n".join(f"- {s.get('title', '')}" for s in sources)
    return answer


if __name__ == "__main__":
    import asyncio

    # Streamable HTTP (2026-07-28 无状态模式), 同时兼容旧协议客户端
    asyncio.run(
        mcp.run_streamable_http_async(
            host="127.0.0.1",
            port=int(os.environ.get("MCP_HTTP_PORT", "8765")),
            stateless_http=True,
        )
    )
