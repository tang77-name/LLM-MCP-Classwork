# -*- coding: utf-8 -*-
"""
chat-stream-tool-file0ps.py

一个支持以下特性的命令行聊天程序（用于创作代码架构图）：

1. 流式输出（Streaming）：逐字打印模型返回的内容。
2. 多轮循环（Loop）：持续对话，直到输入 exit / quit / q 退出。
3. 会话历史（History）：本地维护完整消息历史，每次请求都带给模型。
4. Function Call 工具组「Mermaid 架构图文件操作」（共 4 个）：
       createMermaidDiagram  根据架构描述生成 Mermaid 图源码并保存为 .mmd 纯文本文件
       updateMermaidDiagram  更新已有 .mmd 文件（overwrite / append / replace）
       listMermaidDiagrams   列出目录下所有 .mmd 架构图文件
       readMermaidDiagram    读取某个 .mmd 文件的 Mermaid 源码

   支持的图类型（Mermaid 关键字）：
       flowchart / graph     流程图、代码架构图（最常用）
       sequenceDiagram      时序图
       classDiagram          类图
       stateDiagram-v2       状态图
       erDiagram            实体关系图（ER 图）
       gantt                 甘特图
       pie                   饼图
       mindmap               思维导图
       timeline              时间线

   .mmd 文件为纯文本（UTF-8），可直接被 Mermaid Live Editor、
   VS Code Mermaid 插件、mkdocs/docusaurus 等渲染。

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
import os
import sys

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

# 架构图默认输出目录（相对当前工作目录）
DEFAULT_DIAGRAM_DIR = os.path.join(os.getcwd(), "diagrams")

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
MAX_TOOL_ROUNDS = 8  # 单次提问中工具调用的最大轮次，防止死循环

# 合法的 Mermaid 图类型关键字 -> 中文说明
DIAGRAM_TYPES = {
    "flowchart": "流程图 / 代码架构图",
    "graph": "流程图（旧语法）",
    "sequenceDiagram": "时序图",
    "classDiagram": "类图",
    "stateDiagram-v2": "状态图",
    "erDiagram": "实体关系图（ER 图）",
    "gantt": "甘特图",
    "pie": "饼图",
    "mindmap": "思维导图",
    "timeline": "时间线",
}

SYSTEM_PROMPT = (
    "你是一个擅长绘制代码架构图的 AI 助手。"
    "当用户描述一个系统、模块或代码的结构时，请使用 Mermaid 语法（flowchart 为主）"
    "生成清晰的架构图，并调用 createMermaidDiagram 工具保存为 .mmd 纯文本文件，"
    "不要只把图画在对话里。"
    "当用户要修改已有图时调用 updateMermaidDiagram，"
    "要查看已有图时调用 listMermaidDiagrams 或 readMermaidDiagram。"
    "生成的 Mermaid 源码必须语法正确：第一行为图类型关键字（如 flowchart TD），"
    "节点 ID 只用字母、数字、下划线，中文标签放在方括号或引号中。"
)

# ---------------------------------------------------------------------------
# Function Call 工具定义（JSON Schema）
# ---------------------------------------------------------------------------
CREATE_DIAGRAM_TOOL = {
    "type": "function",
    "function": {
        "name": "createMermaidDiagram",
        "description": (
            "创作一张代码架构图（Mermaid 格式），并保存为 .mmd 纯文本文件。"
            "filename 为文件名（可不带扩展名，自动补 .mmd），"
            "content 为完整的 Mermaid 源码。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "文件名，例如 'system-arch' 或 'system-arch.mmd'。",
                },
                "content": {
                    "type": "string",
                    "description": "完整的 Mermaid 源码，例如 'flowchart TD\\n  A[客户端] --> B[网关]'。",
                },
                "diagram_type": {
                    "type": "string",
                    "description": "可选。图类型，用于校验源码首行关键字。",
                    "enum": list(DIAGRAM_TYPES.keys()),
                },
                "directory": {
                    "type": "string",
                    "description": "可选。保存目录，默认当前目录下的 diagrams/。",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "可选。同名文件已存在时是否覆盖，默认 false。",
                },
            },
            "required": ["filename", "content"],
        },
    },
}

UPDATE_DIAGRAM_TOOL = {
    "type": "function",
    "function": {
        "name": "updateMermaidDiagram",
        "description": (
            "更新已有的 .mmd 架构图文件。"
            "mode=overwrite 整体覆盖；mode=append 在末尾追加；"
            "mode=replace 把文件中所有 old_str 替换为 new_str。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": ".mmd 文件名或路径，例如 'system-arch.mmd'。",
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append", "replace"],
                    "description": "编辑方式，默认 overwrite。",
                },
                "content": {
                    "type": "string",
                    "description": "overwrite/append 模式下要写入的 Mermaid 源码。",
                },
                "old_str": {
                    "type": "string",
                    "description": "replace 模式下要被替换的原字符串（需唯一存在）。",
                },
                "new_str": {
                    "type": "string",
                    "description": "replace 模式下替换成的新字符串。",
                },
            },
            "required": ["filename", "mode"],
        },
    },
}

LIST_DIAGRAMS_TOOL = {
    "type": "function",
    "function": {
        "name": "listMermaidDiagrams",
        "description": "列出指定目录下所有 .mmd 架构图文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "可选。目录路径，默认当前目录下的 diagrams/。",
                }
            },
            "required": [],
        },
    },
}

READ_DIAGRAM_TOOL = {
    "type": "function",
    "function": {
        "name": "readMermaidDiagram",
        "description": "读取指定 .mmd 文件中的 Mermaid 源码内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": ".mmd 文件名或路径，例如 'system-arch.mmd'。",
                }
            },
            "required": ["filename"],
        },
    },
}

TOOLS = [
    CREATE_DIAGRAM_TOOL,
    UPDATE_DIAGRAM_TOOL,
    LIST_DIAGRAMS_TOOL,
    READ_DIAGRAM_TOOL,
]


# ---------------------------------------------------------------------------
# 工具实现：Mermaid 架构图 .mmd 文件操作
# ---------------------------------------------------------------------------
def _ensure_mmd(filename: str) -> str:
    """规范化文件名：去掉首尾空白，自动补 .mmd 扩展名。"""
    name = str(filename or "").strip()
    if not name:
        raise ValueError("filename 不能为空")
    if not name.lower().endswith(".mmd"):
        name += ".mmd"
    return name


def _diagram_path(filename: str, directory: str = None) -> str:
    """把文件名解析成绝对路径：
    - 绝对路径：直接使用；
    - 带目录部分的相对路径：基于当前工作目录解析；
    - 纯文件名：落在默认 diagrams 目录（或指定 directory）下。
    """
    name = _ensure_mmd(filename)
    if os.path.isabs(name):
        return name
    if os.path.dirname(name):  # 例如 'out/x.mmd'
        return os.path.abspath(name)
    base = directory.strip() if directory and str(directory).strip() else DEFAULT_DIAGRAM_DIR
    return os.path.abspath(os.path.join(base, name))


def _validate_mermaid(content: str, diagram_type: str = None) -> None:
    """校验 Mermaid 源码的基本结构（首行图类型关键字），不合法则抛 ValueError。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("Mermaid 源码不能为空")

    # 跳过注释行（%% 开头）后取第一行
    first_line = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            first_line = stripped
            break

    head = first_line.split()[0] if first_line.split() else ""
    if head not in DIAGRAM_TYPES:
        raise ValueError(
            f"Mermaid 源码首行必须是图类型关键字之一：{', '.join(DIAGRAM_TYPES)}；"
            f"实际首行为 {first_line!r}"
        )
    if diagram_type and head != diagram_type and not (
        {head, diagram_type} <= {"flowchart", "graph"}
    ):
        raise ValueError(f"声明的 diagram_type={diagram_type} 与源码首行关键字 {head} 不一致")


def create_mermaid_diagram(filename: str, content: str, diagram_type: str = None,
                           directory: str = None, overwrite: bool = False) -> str:
    """createMermaidDiagram：生成架构图并保存为 .mmd 纯文本文件。"""
    _validate_mermaid(content, diagram_type)

    path = _diagram_path(filename, directory)
    if os.path.isdir(path):
        raise ValueError(f"同名目录已存在，无法保存文件 -> {path}")
    if os.path.exists(path) and not overwrite:
        raise ValueError(f"文件已存在（如需覆盖请设置 overwrite=true）-> {path}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 统一保存为 UTF-8 无 BOM 纯文本，保证任何 Mermaid 工具都能直接渲染
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content.rstrip("\n") + "\n")

    lines = content.count("\n") + 1
    return f"架构图已保存: {path}（Mermaid 纯文本，{lines} 行，{os.path.getsize(path)} 字节）"


def update_mermaid_diagram(filename: str, mode: str = "overwrite", content: str = "",
                           old_str: str = None, new_str: str = None) -> str:
    """updateMermaidDiagram：更新已有的 .mmd 文件（overwrite/append/replace）。"""
    path = _diagram_path(filename)
    if not os.path.isfile(path):
        raise ValueError(f".mmd 文件不存在 -> {path}")

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    mode = (mode or "overwrite").lower()
    if mode == "overwrite":
        new_text = content or ""
        _validate_mermaid(new_text)
        summary = "整体覆盖"
    elif mode == "append":
        new_text = text.rstrip("\n") + "\n" + (content or "")
        summary = "追加内容"
    elif mode == "replace":
        if old_str is None or new_str is None:
            raise ValueError("replace 模式必须同时提供 old_str 和 new_str")
        if old_str == "":
            raise ValueError("old_str 不能为空")
        count = text.count(old_str)
        if count == 0:
            raise ValueError(f"文件中未找到 old_str -> {old_str!r}")
        if count > 1:
            raise ValueError(f"old_str 在文件中出现 {count} 次，为避免误改请提供唯一片段")
        new_text = text.replace(old_str, new_str)
        summary = "替换 1 处内容"
    else:
        raise ValueError(f"未知 mode -> {mode}（支持 overwrite/append/replace）")

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new_text.rstrip("\n") + "\n")

    return f"{summary}成功: {path}（{os.path.getsize(path)} 字节）"


def list_mermaid_diagrams(directory: str = None) -> str:
    """listMermaidDiagrams：列出目录下所有 .mmd 文件。"""
    base = directory.strip() if directory and str(directory).strip() else DEFAULT_DIAGRAM_DIR
    base = os.path.abspath(os.path.expanduser(base))

    if not os.path.isdir(base):
        raise ValueError(f"目录不存在 -> {base}（可先调用 createMermaidDiagram 创建）")

    files = sorted(
        (e for e in os.scandir(base) if e.is_file() and e.name.lower().endswith(".mmd")),
        key=lambda e: e.name.lower(),
    )
    if not files:
        return f"目录 {base} 下没有 .mmd 架构图文件。"

    lines = [f"目录 {base} 共 {len(files)} 张架构图："]
    for entry in files:
        lines.append(f"  - {entry.name} ({entry.stat().st_size} 字节)")
    return "\n".join(lines)


def read_mermaid_diagram(filename: str) -> str:
    """readMermaidDiagram：读取 .mmd 文件中的 Mermaid 源码。"""
    path = _diagram_path(filename)
    if not os.path.isfile(path):
        raise ValueError(f".mmd 文件不存在 -> {path}")

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return f"{path} 的 Mermaid 源码：\n{text.strip()}"


# ---------------------------------------------------------------------------
# 工具分发
# ---------------------------------------------------------------------------
def execute_tool(name: str, arguments: dict) -> str:
    """根据工具名分发执行，返回给模型的字符串结果。"""
    args = arguments or {}
    try:
        if name == "createMermaidDiagram":
            return create_mermaid_diagram(
                args.get("filename"),
                args.get("content", ""),
                args.get("diagram_type"),
                args.get("directory"),
                bool(args.get("overwrite", False)),
            )
        if name == "updateMermaidDiagram":
            return update_mermaid_diagram(
                args.get("filename"),
                args.get("mode", "overwrite"),
                args.get("content", ""),
                args.get("old_str"),
                args.get("new_str"),
            )
        if name == "listMermaidDiagrams":
            return list_mermaid_diagrams(args.get("directory"))
        if name == "readMermaidDiagram":
            return read_mermaid_diagram(args.get("filename"))
    except ValueError as exc:
        return f"错误：{exc}"
    except OSError as exc:
        return f"错误：文件操作失败 -> {exc}"
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
        "tools": TOOLS,
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
    print(" 流式多轮聊天（Mermaid 代码架构图创作工具）")
    print(f" 模型: {MODEL} | 接口: {CHAT_URL}")
    print(f" 架构图保存目录: {DEFAULT_DIAGRAM_DIR}")
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
