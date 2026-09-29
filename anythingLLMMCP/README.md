# AnythingLLM MCP Server

基于 **MCP 2026-07-28** 协议的 MCP Server(Python SDK v2),访问本机 AnythingLLM。
提供一个工具:自动选取**第一个工作区**,基于其中嵌入的知识进行 AI 回答。

- 工具 `ask_workspace(message)`:取第一个工作区,调用其 chat 接口(RAG)回答问题,返回回答及来源。
- 传输:**Streamable HTTP(无状态模式)**,地址 `http://127.0.0.1:8765/mcp`。

## 项目结构

```
E:\anythingLLMMCP
├── .mcp.json          # 项目级 MCP 配置(客户端读取此文件)
├── server.py          # MCP 服务端
├── .venv\             # 项目虚拟环境(已装 mcp,自包含,不依赖系统 Python)
├── requirements.txt
└── test_http.py / test_inproc.py   # 验证脚本
```

## 项目级 MCP 配置

项目根目录的 `.mcp.json` 已把本服务注册为项目级 MCP(名 `anythingllm`,HTTP 类型):

```json
{
  "mcpServers": {
    "anythingllm": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

支持读取项目级 `.mcp.json` 的 MCP 客户端(Claude Desktop、Cursor 等)打开本目录后会自动识别。
**前提:本服务已在运行**(见下方启动说明),客户端通过 URL 连接。

## 启动 / 停止

### 启动(前台,占用终端)

```powershell
E:\anythingLLMMCP\.venv\Scripts\python.exe E:\anythingLLMMCP\server.py
```

按 `Ctrl+C` 停止。

### 启动(后台)

```powershell
Start-Process -FilePath "E:\anythingLLMMCP\.venv\Scripts\python.exe" -ArgumentList "E:\anythingLLMMCP\server.py" -WindowStyle Hidden
```

### 停止

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*anythingLLMMCP*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

### 验证服务在运行

```powershell
# 查进程
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*anythingLLMMCP*server.py*' } | Select-Object ProcessId, CreationDate

# 或探活(返回 2026-07-28 即正常)
python E:\anythingLLMMCP\test_http.py
```

## 安装(首次 / 换机器)

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 配置(环境变量,均有默认值)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ANYLLM_BASE_URL` | `http://localhost:3001` | AnythingLLM 地址 |
| `ANYLLM_API_KEY` | `4HXAD7P-6Y8MF8X-QBXZD8A-E5037CM` | AnythingLLM API Key |
| `MCP_HTTP_PORT` | `8765` | MCP 服务监听端口 |
