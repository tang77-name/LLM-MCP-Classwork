# -*- coding: utf-8 -*-
"""
chat-stream-loop-history.py

一个支持以下特性的命令行聊天程序：

1. 流式输出（Streaming）：逐字打印模型返回的内容。
2. 多轮循环（Loop）：持续对话，直到输入 exit / quit / q 退出。
3. 会话历史（History）：本地维护完整消息历史，每次请求都带给模型。
4. Function Call 工具 getDate：通过调用本地操作系统的 date 命令
   （Windows 下为 `date /t` 与 `time /t`）来获取当前电脑的日期和时间，
   而不是用 Python 代码（如 datetime）计算。

依赖：requests （pip install requests）

配置（优先级从高到低）：
    1. 环境变量 OPENAI_API_KEY / OPENAI_BASE_URL / CHAT_MODEL
    2. 本文件同目录下 config.ini 的 [llm] 段：
           [llm]
           base_url = https://api.deepseek.com
           api_key = sk-xxxx
           model_name = deepseek-chat
    3. 内置默认值：DeepSeek 官方接口
"""

import configparser
import json
import locale
import os
import subprocess
import sys

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

# 内置默认配置（DeepSeek）
DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com",
    # 出于安全，此处不硬编码真实 key：请写在 config.ini 或环境变量中
    "api_key": "EMPTY",
    "model_name": "deepseek-chat",
}


def load_config() -> dict:
    """读取配置：环境变量 > config.ini [llm] > 内置默认值。"""
    cfg = dict(DEFAULT_CONFIG)

    if os.path.isfile(CONFIG_FILE):
        parser = configparser.ConfigParser()
        try:
            parser.read(CONFIG_FILE, encoding="utf-8")
            if parser.has_section("llm"):
                for key in cfg:
                    if parser.has_option("llm", key):
                        cfg[key] = parser.get("llm", key).strip()
        except configparser.Error as exc:
            print(f"[警告] 解析 {CONFIG_FILE} 失败，使用默认配置: {exc}", file=sys.stderr)

    # 环境变量优先
    cfg["api_key"] = os.environ.get("OPENAI_API_KEY", cfg["api_key"])
    cfg["base_url"] = os.environ.get("OPENAI_BASE_URL", cfg["base_url"])
    cfg["model_name"] = os.environ.get("CHAT_MODEL", cfg["model_name"])
    return cfg


_CONFIG = load_config()
API_KEY = _CONFIG["api_key"]
BASE_URL = _CONFIG["base_url"].rstrip("/")
MODEL = _CONFIG["model_name"]

CHAT_URL = f"{BASE_URL}/chat/completions"
TIMEOUT = 60
MAX_TOOL_ROUNDS = 5  # 单次提问中工具调用的最大轮次，防止死循环

SYSTEM_PROMPT = (
    "你是一个乐于助人的 AI 助手。当用户询问当前日期或时间时，"
    "请调用 getDate 工具获取真实时间，不要凭记忆作答。"
)

# getDate 工具的 JSON Schema（Function Calling 定义）
GET_DATE_TOOL = {
    "type": "function",
    "function": {
        "name": "getDate",
        "description": (
            "获取当前电脑（本地机器）的日期和时间。"
            "内部通过调用操作系统的 date 命令实现，"
            "可用于回答“现在几点”“今天几号”等问题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "description": "可选。期望的输出格式提示，例如 'full'、'date'、'time'。",
                    "enum": ["full", "date", "time"],
                }
            },
            "required": [],
        },
    },
}


# ---------------------------------------------------------------------------
# 工具实现：调用本地 date 命令获取日期时间
# ---------------------------------------------------------------------------
def run_local_date_command() -> str:
    """调用本机的 date 命令获取日期和时间（不使用 Python 代码计算）。

    - Windows: 执行 `date /t` 和 `time /t`（通过 cmd）
    - 其他系统: 执行 `date`
    """
    commands = []
    if os.name == "nt":
        commands.append(["cmd", "/c", "date /t"])
        commands.append(["cmd", "/c", "time /t"])
    else:
        commands.append(["date"])

    outputs = []
    # Windows 下 cmd 输出使用系统 ANSI 代码页（如中文环境为 cp936/GBK）。
    # 注意不能用 locale.getpreferredencoding()：在 UTF-8 模式下它会返回 utf-8 而导致乱码。
    encoding = "mbcs" if os.name == "nt" else (
        locale.getpreferredencoding(False) or "utf-8"
    )
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                encoding=encoding,
                errors="replace",
                timeout=10,
                check=False,
            )
            text = (result.stdout or result.stderr).strip()
            if text:
                outputs.append(text)
        except Exception as exc:  # 命令执行失败时给出可读信息
            outputs.append(f"执行命令 {' '.join(cmd)} 失败: {exc}")

    return "\n".join(outputs) if outputs else "无法通过本地 date 命令获取日期时间"


def get_date(format_hint: str = "full") -> str:
    """getDate 工具入口：调用本地 date 命令并按需裁剪输出。"""
    raw = run_local_date_command()
    if format_hint == "date":
        return raw.splitlines()[0] if raw else raw
    if format_hint == "time":
        lines = raw.splitlines()
        return lines[-1] if len(lines) > 1 else raw
    return raw


def execute_tool(name: str, arguments: dict) -> str:
    """根据工具名分发执行，返回给模型的字符串结果。"""
    if name == "getDate":
        return get_date(arguments.get("format", "full"))
    return f"错误：未知工具 {name}"


# ---------------------------------------------------------------------------
# 流式请求
# ---------------------------------------------------------------------------
def stream_chat(messages: list) -> dict:
    """发送流式请求，边接收边打印增量内容。

    返回一个汇总结果：
        {"content": 完整文本, "tool_calls": 工具调用列表, "finish_reason": 结束原因}
    """
    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": [GET_DATE_TOOL],
        "tool_choice": "auto",
        "stream": True,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    content_parts = []
    # 按 tool_call 索引收集增量片段：{index: {"id","name","arguments"}}
    tool_call_buf = {}
    finish_reason = None

    with requests.post(
        CHAT_URL, json=payload, headers=headers, stream=True, timeout=TIMEOUT
    ) as resp:
        if resp.status_code != 200:
            raise RuntimeError(
                f"请求失败 HTTP {resp.status_code}: {resp.text[:500]}"
            )
        # SSE 通常只声明 text/event-stream 而无 charset，
        # requests 默认会用 ISO-8859-1 解码导致中文乱码，这里强制 UTF-8
        resp.encoding = "utf-8"

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue  # SSE 空行
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if chunk.get("error"):
                raise RuntimeError(str(chunk["error"]))

            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}

            # 1) 普通文本增量 -> 实时打印
            text = delta.get("content")
            if text:
                content_parts.append(text)
                print(text, end="", flush=True)

            # 2) 工具调用增量 -> 按 index 拼接
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                entry = tool_call_buf.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""}
                )
                fn = tc.get("function") or {}
                if tc.get("id"):
                    entry["id"] = tc["id"]
                if fn.get("name"):
                    entry["name"] += fn["name"]
                if fn.get("arguments"):
                    entry["arguments"] += fn["arguments"]

            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

    if content_parts:
        print()  # 换行

    tool_calls = [tool_call_buf[i] for i in sorted(tool_call_buf)]
    return {
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
    }


# ---------------------------------------------------------------------------
# 工具调用轮次处理
# ---------------------------------------------------------------------------
def build_assistant_message(result: dict) -> dict:
    """把流式汇总结果还原为一条 assistant 消息。"""
    msg = {"role": "assistant", "content": result["content"] or None}
    if result["tool_calls"]:
        msg["tool_calls"] = [
            {
                "id": tc["id"] or f"call_{i}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"] or "{}",
                },
            }
            for i, tc in enumerate(result["tool_calls"])
        ]
    return msg


def handle_one_question(history: list, question: str) -> None:
    """处理一次用户提问（含可能的多轮工具调用），并把结果写入历史。"""
    history.append({"role": "user", "content": question})

    for _round in range(MAX_TOOL_ROUNDS):
        result = stream_chat(history)
        history.append(build_assistant_message(result))

        if not result["tool_calls"]:
            return  # 没有工具调用，本次问答完成

        # 执行所有工具调用，把结果回传给模型
        for tc in result["tool_calls"]:
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
                output = f"错误：参数不是合法 JSON -> {tc['arguments']}"
            else:
                print(f"[调用工具] {name}({args})", flush=True)
                output = execute_tool(name, args)
            print(f"[工具结果] {output}", flush=True)

            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"] or "call_0",
                    "name": name,
                    "content": output,
                }
            )

    print("[警告] 工具调用轮次达到上限，已停止本轮。", file=sys.stderr)


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 60)
    print(" 流式多轮聊天（支持历史记录 + getDate 工具）")
    print(f" 模型: {MODEL} | 接口: {CHAT_URL}")
    print(" 输入 exit / quit / q 结束，clear 清空历史")
    print("=" * 60)

    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            question = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            print("已退出。")
            break
        if question.lower() == "clear":
            history = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("历史记录已清空。")
            continue

        try:
            handle_one_question(history, question)
        except requests.RequestException as exc:
            print(f"[网络错误] {exc}", file=sys.stderr)
        except RuntimeError as exc:
            print(f"[接口错误] {exc}", file=sys.stderr)

        # 调试：显示当前历史条数
        print(f"[历史] 共 {len(history)} 条消息", file=sys.stderr)


if __name__ == "__main__":
    main()
