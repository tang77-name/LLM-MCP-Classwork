# -*- coding: utf-8 -*-
"""
chat-stream-tool-isLeapYear.py

一个支持以下特性的命令行聊天程序：

1. 流式输出（Streaming）：逐字打印模型返回的内容。
2. 多轮循环（Loop）：持续对话，直到输入 exit / quit / q 退出。
3. 会话历史（History）：本地维护完整消息历史，每次请求都带给模型。
4. Function Call 工具 isLeapYear：判断输入年份是否为闰年，
   由 Python 代码计算得出（能被 4 整除但不能被 100 整除，或能被 400 整除）。
5. Function Call 工具组「本地文件操作」（共 10 个）：
       getCwd              1. 获取当前工作目录
       listDirectory       2. 列出指定目录下所有文件和子目录
       getFileType         3. 获取指定文件的文件类型
       readFile            4. 获取指定文件的内容（仅支持纯文本格式文件）
       createFile          5. 根据指定的目录和文件名创建文件
       editFile            6. 编辑指定文件的内容（仅支持纯文本格式文件）
       createDirectory     7. 创建新目录
       deleteDirectory     8. 删除目录（非空）
       deleteFile          9. 删除文件
       renamePath          10. 重命名文件和目录

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

import calendar
import configparser
import json
import mimetypes
import os
import shutil
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
MAX_TOOL_ROUNDS = 8  # 单次提问中工具调用的最大轮次，防止死循环
MAX_READ_BYTES = 200_000  # 读取纯文本文件时的上限，约 200 KB

SYSTEM_PROMPT = (
    "你是一个乐于助人的 AI 助手。"
    "当用户询问某一年是否是闰年时，请调用 isLeapYear 工具进行计算，不要凭记忆作答。"
    "当用户需要查看、创建、编辑、删除、重命名本地文件或目录，"
    "或需要知道当前工作目录、目录内容、文件类型时，"
    "请调用对应的本地文件操作工具（getCwd、listDirectory、getFileType、readFile、"
    "createFile、editFile、createDirectory、deleteDirectory、deleteFile、renamePath）"
    "完成操作，不要凭记忆作答。"
    "读取和编辑文件内容仅支持纯文本格式；"
    "执行删除、覆盖等破坏性操作前，先向用户确认。"
)

# ---------------------------------------------------------------------------
# Function Call 工具定义（JSON Schema）
# ---------------------------------------------------------------------------
PATH_PROP = {
    "type": "string",
    "description": "文件或目录路径。相对路径基于当前工作目录解析，例如 'data/a.txt'。",
}

IS_LEAP_YEAR_TOOL = {
    "type": "function",
    "function": {
        "name": "isLeapYear",
        "description": (
            "判断输入的年份是否是闰年。"
            "内部通过 Python 代码按闰年规则计算得出，"
            "可用于回答“2024 年是不是闰年”“哪一年是闰年”等问题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "要判断的年份，例如 2024。",
                }
            },
            "required": ["year"],
        },
    },
}

# 1. 获取当前工作目录
GET_CWD_TOOL = {
    "type": "function",
    "function": {
        "name": "getCwd",
        "description": "获取当前程序的工作目录（绝对路径）。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# 2. 列出指定目录下所有文件和子目录
LIST_DIRECTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "listDirectory",
        "description": "列出指定目录下的所有文件和子目录（含类型与大小）。",
        "parameters": {
            "type": "object",
            "properties": {"path": dict(PATH_PROP)},
            "required": ["path"],
        },
    },
}

# 3. 获取指定文件的文件类型
GET_FILE_TYPE_TOOL = {
    "type": "function",
    "function": {
        "name": "getFileType",
        "description": (
            "获取指定文件的文件类型：扩展名、MIME 类型、"
            "是文件还是目录、大小以及是纯文本还是二进制。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": dict(PATH_PROP)},
            "required": ["path"],
        },
    },
}

# 4. 获取指定文件的内容（仅支持纯文本格式文件）
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "readFile",
        "description": "获取指定文件的内容，仅支持纯文本格式文件（二进制文件会拒绝读取）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": dict(PATH_PROP),
                "max_chars": {
                    "type": "integer",
                    "description": "可选。最多返回多少个字符，默认 4000，用于防止内容过长。",
                },
            },
            "required": ["path"],
        },
    },
}

# 5. 根据指定的目录和文件名创建文件
CREATE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "createFile",
        "description": "根据指定的目录和文件名创建文件，可选写入初始内容（纯文本）。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "文件所在的目录路径，例如 'docs'；可用 '.' 表示当前目录。",
                },
                "filename": {
                    "type": "string",
                    "description": "文件名（含扩展名），例如 'notes.txt'。",
                },
                "content": {
                    "type": "string",
                    "description": "可选。写入文件的初始纯文本内容，缺省创建空文件。",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "可选。文件已存在时是否覆盖，默认 false。",
                },
            },
            "required": ["directory", "filename"],
        },
    },
}

# 6. 编辑指定文件的内容（仅支持纯文本格式文件）
EDIT_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "editFile",
        "description": (
            "编辑指定文件的内容，仅支持纯文本格式文件。"
            "mode=overwrite 整体覆盖；mode=append 追加到末尾；"
            "mode=replace 把文件中所有 old_str 替换为 new_str。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": dict(PATH_PROP),
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append", "replace"],
                    "description": "编辑方式，默认 overwrite。",
                },
                "content": {
                    "type": "string",
                    "description": "overwrite/append 模式下要写入的纯文本内容。",
                },
                "old_str": {
                    "type": "string",
                    "description": "replace 模式下要被替换的原字符串（需在文件中存在且唯一）。",
                },
                "new_str": {
                    "type": "string",
                    "description": "replace 模式下替换成的新字符串。",
                },
            },
            "required": ["path", "mode"],
        },
    },
}

# 7. 创建新目录
CREATE_DIRECTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "createDirectory",
        "description": "创建新目录（含多级父目录），目录已存在时不报错。",
        "parameters": {
            "type": "object",
            "properties": {"path": dict(PATH_PROP)},
            "required": ["path"],
        },
    },
}

# 8. 删除目录（非空）
DELETE_DIRECTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "deleteDirectory",
        "description": "删除指定目录及其全部内容（支持非空目录，递归删除，不可恢复）。",
        "parameters": {
            "type": "object",
            "properties": {"path": dict(PATH_PROP)},
            "required": ["path"],
        },
    },
}

# 9. 删除文件
DELETE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "deleteFile",
        "description": "删除指定的文件（不可恢复）。",
        "parameters": {
            "type": "object",
            "properties": {"path": dict(PATH_PROP)},
            "required": ["path"],
        },
    },
}

# 10. 重命名文件和目录
RENAME_PATH_TOOL = {
    "type": "function",
    "function": {
        "name": "renamePath",
        "description": "重命名（或移动）指定的文件或目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "old_path": dict(PATH_PROP),
                "new_path": {
                    "type": "string",
                    "description": "新的路径（文件或目录的新名字/新位置）。",
                },
            },
            "required": ["old_path", "new_path"],
        },
    },
}

TOOLS = [
    IS_LEAP_YEAR_TOOL,
    GET_CWD_TOOL,
    LIST_DIRECTORY_TOOL,
    GET_FILE_TYPE_TOOL,
    READ_FILE_TOOL,
    CREATE_FILE_TOOL,
    EDIT_FILE_TOOL,
    CREATE_DIRECTORY_TOOL,
    DELETE_DIRECTORY_TOOL,
    DELETE_FILE_TOOL,
    RENAME_PATH_TOOL,
]


# ---------------------------------------------------------------------------
# 工具实现 1：用 Python 代码判断闰年
# ---------------------------------------------------------------------------
def is_leap_year(year) -> str:
    """isLeapYear 工具入口：用 Python 代码判断指定年份是否为闰年。

    闰年规则（公历格里高利历）：
        1. 能被 4 整除但不能被 100 整除；或者
        2. 能被 400 整除。
    """
    try:
        y = int(year)
    except (TypeError, ValueError):
        return f"错误：year 必须是整数年份，收到的是 {year!r}"

    rule_leap = (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)
    lib_leap = calendar.isleap(y)
    assert rule_leap == lib_leap, "闰年计算自检失败"

    reason = (
        "能被4整除但不能被100整除" if (y % 4 == 0 and y % 100 != 0)
        else "能被400整除" if (y % 400 == 0)
        else "不能被4整除" if (y % 4 != 0)
        else "能被4整除但也能被100整除且不能被400整除"
    )
    verdict = "是闰年" if rule_leap else "不是闰年"
    return f"{y} 年{verdict}（{reason}）。"


# ---------------------------------------------------------------------------
# 工具实现 2：本地文件操作
# ---------------------------------------------------------------------------
# 常见纯文本扩展名（白名单，命中即视为纯文本）
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".log", ".csv", ".tsv", ".ini", ".cfg", ".conf",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".html", ".htm", ".css",
    ".js", ".ts", ".py", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go",
    ".rs", ".sh", ".bat", ".ps1", ".sql", ".bat", ".properties", ".gitignore",
}


def _resolve(path: str) -> str:
    """把相对路径解析为绝对路径（不做符号链接归一化，保持可读性）。"""
    if not path or not str(path).strip():
        raise ValueError("路径不能为空")
    return os.path.abspath(os.path.expanduser(str(path).strip()))


def _decode_text(raw: bytes) -> str:
    """尝试把字节流按常见编码解码为文本，失败则认为是二进制。"""
    if b"\x00" in raw:
        raise ValueError("文件包含二进制内容（NUL 字节）")
    for enc in ("utf-8-sig", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("文件不是可识别的纯文本编码（UTF-8/GBK/UTF-16）")


def _read_text(path: str, max_bytes: int = None) -> str:
    """读取纯文本文件内容；二进制文件抛出 ValueError。"""
    with open(path, "rb") as fh:
        raw = fh.read(max_bytes) if max_bytes else fh.read()
    return _decode_text(raw)


def _is_text_file(path: str) -> bool:
    """判断一个已存在的文件是否为纯文本（扩展名白名单 + 内容嗅探）。"""
    if os.path.splitext(path)[1].lower() in TEXT_EXTENSIONS:
        # 白名单也做一次内容校验，避免被改造成二进制
        try:
            with open(path, "rb") as fh:
                _decode_text(fh.read(8192))
            return True
        except (OSError, ValueError):
            return False
    try:
        with open(path, "rb") as fh:
            _decode_text(fh.read(8192))
        return True
    except (OSError, ValueError):
        return False


def _format_size(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"


def get_cwd() -> str:
    """getCwd：获取当前工作目录。"""
    return f"当前工作目录: {os.getcwd()}"


def list_directory(path: str) -> str:
    """listDirectory：列出指定目录下所有文件和子目录。"""
    target = _resolve(path)
    if not os.path.exists(target):
        return f"错误：目录不存在 -> {target}"
    if not os.path.isdir(target):
        return f"错误：不是目录 -> {target}"

    entries = sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower()))
    if not entries:
        return f"目录 {target} 为空。"

    lines = [f"目录 {target} 共 {len(entries)} 项："]
    for entry in entries:
        if entry.is_dir():
            lines.append(f"  [目录] {entry.name}/")
        else:
            try:
                size = _format_size(entry.stat().st_size)
            except OSError:
                size = "大小未知"
            lines.append(f"  [文件] {entry.name} ({size})")
    return "\n".join(lines)


def get_file_type(path: str) -> str:
    """getFileType：获取指定文件的文件类型。"""
    target = _resolve(path)
    if not os.path.exists(target):
        return f"错误：路径不存在 -> {target}"

    name = os.path.basename(target)
    ext = os.path.splitext(name)[1] or "(无扩展名)"
    mime, _ = mimetypes.guess_type(name)

    if os.path.isdir(target):
        kind = "目录"
        try:
            count = len(os.listdir(target))
        except OSError:
            count = -1
        info = f"条目数: {count}"
    else:
        kind = "文件"
        size = _format_size(os.path.getsize(target))
        text_or_bin = "纯文本" if _is_text_file(target) else "二进制"
        info = f"大小: {size} | 内容: {text_or_bin}"

    return (
        f"路径: {target}\n"
        f"类型: {kind} | 扩展名: {ext} | MIME: {mime or '未知'}\n"
        f"{info}"
    )


def read_file(path: str, max_chars: int = 4000) -> str:
    """readFile：读取纯文本文件内容（拒绝二进制文件）。"""
    target = _resolve(path)
    if not os.path.exists(target):
        return f"错误：文件不存在 -> {target}"
    if os.path.isdir(target):
        return f"错误：这是目录而不是文件 -> {target}"
    if not _is_text_file(target):
        return f"错误：仅支持读取纯文本格式文件 -> {target}"

    try:
        text = _read_text(target, max_bytes=MAX_READ_BYTES)
    except ValueError as exc:
        return f"错误：{exc} -> {target}"
    except OSError as exc:
        return f"错误：读取失败 -> {exc}"

    truncated = ""
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
        truncated = f"\n...（内容过长，已截断，共 {len(text)}+ 字符）"
    return f"文件 {target} 内容：\n{text}{truncated}"


def create_file(directory: str, filename: str, content: str = "",
                overwrite: bool = False) -> str:
    """createFile：根据指定的目录和文件名创建文件。"""
    if not filename or not str(filename).strip():
        return "错误：filename 不能为空"
    if os.path.basename(str(filename).strip()) != str(filename).strip():
        return f"错误：filename 只能是文件名，不能包含路径分隔符 -> {filename}"

    dir_path = _resolve(directory or ".")
    file_path = os.path.join(dir_path, str(filename).strip())

    if os.path.isdir(file_path):
        return f"错误：同名目录已存在 -> {file_path}"
    if os.path.exists(file_path) and not overwrite:
        return f"错误：文件已存在（如需覆盖请设置 overwrite=true）-> {file_path}"

    try:
        os.makedirs(dir_path, exist_ok=True)
        with open(file_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content or "")
    except OSError as exc:
        return f"错误：创建文件失败 -> {exc}"

    action = "覆盖创建" if os.path.exists(file_path) and overwrite else "创建"
    return f"{action}文件成功: {file_path}（{_format_size(os.path.getsize(file_path))}）"


def edit_file(path: str, mode: str = "overwrite", content: str = "",
              old_str: str = None, new_str: str = None) -> str:
    """editFile：编辑纯文本文件（overwrite / append / replace）。"""
    target = _resolve(path)
    if not os.path.exists(target):
        return f"错误：文件不存在 -> {target}"
    if os.path.isdir(target):
        return f"错误：这是目录而不是文件 -> {target}"
    if not _is_text_file(target):
        return f"错误：仅支持编辑纯文本格式文件 -> {target}"

    try:
        text = _read_text(target)
    except ValueError as exc:
        return f"错误：{exc} -> {target}"

    mode = (mode or "overwrite").lower()
    if mode == "overwrite":
        new_text = content or ""
        summary = "覆盖写入"
    elif mode == "append":
        new_text = text + (content or "")
        summary = "追加内容"
    elif mode == "replace":
        if old_str is None or new_str is None:
            return "错误：replace 模式必须同时提供 old_str 和 new_str"
        if old_str == "":
            return "错误：old_str 不能为空"
        count = text.count(old_str)
        if count == 0:
            return f"错误：文件中未找到 old_str -> {old_str!r}"
        if count > 1:
            return f"错误：old_str 在文件中出现 {count} 次，为避免误改请提供唯一片段"
        new_text = text.replace(old_str, new_str)
        summary = "替换 1 处内容"
    else:
        return f"错误：未知 mode -> {mode}（支持 overwrite/append/replace）"

    try:
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
    except OSError as exc:
        return f"错误：写入失败 -> {exc}"

    return f"{summary}成功: {target}（{_format_size(os.path.getsize(target))}）"


def create_directory(path: str) -> str:
    """createDirectory：创建新目录（含多级目录）。"""
    target = _resolve(path)
    if os.path.isfile(target):
        return f"错误：同名文件已存在，无法创建目录 -> {target}"
    exist_before = os.path.isdir(target)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return f"错误：创建目录失败 -> {exc}"
    note = "目录已存在，无需重复创建" if exist_before else "创建目录成功"
    return f"{note}: {target}"


def delete_directory(path: str) -> str:
    """deleteDirectory：递归删除目录（支持非空目录）。"""
    target = _resolve(path)
    if not os.path.exists(target):
        return f"错误：目录不存在 -> {target}"
    if not os.path.isdir(target):
        return f"错误：这是文件而不是目录，请用 deleteFile -> {target}"

    try:
        entry_count = sum(len(files) for _, _, files in os.walk(target))
        shutil.rmtree(target)
    except OSError as exc:
        return f"错误：删除目录失败 -> {exc}"
    return f"删除目录成功（含约 {entry_count} 个文件）: {target}"


def delete_file(path: str) -> str:
    """deleteFile：删除指定文件。"""
    target = _resolve(path)
    if not os.path.exists(target):
        return f"错误：文件不存在 -> {target}"
    if os.path.isdir(target):
        return f"错误：这是目录而不是文件，请用 deleteDirectory -> {target}"
    try:
        size = os.path.getsize(target)
        os.remove(target)
    except OSError as exc:
        return f"错误：删除文件失败 -> {exc}"
    return f"删除文件成功（原大小 {_format_size(size)}）: {target}"


def rename_path(old_path: str, new_path: str) -> str:
    """renamePath：重命名（或移动）文件和目录。"""
    src = _resolve(old_path)
    dst = _resolve(new_path)
    if not os.path.exists(src):
        return f"错误：源路径不存在 -> {src}"
    if os.path.exists(dst):
        return f"错误：目标路径已存在 -> {dst}"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    except OSError as exc:
        return f"错误：重命名失败 -> {exc}"
    kind = "目录" if os.path.isdir(dst) else "文件"
    return f"重命名{kind}成功: {src} -> {dst}"


# ---------------------------------------------------------------------------
# 工具分发
# ---------------------------------------------------------------------------
def execute_tool(name: str, arguments: dict) -> str:
    """根据工具名分发执行，返回给模型的字符串结果。"""
    args = arguments or {}
    try:
        if name == "isLeapYear":
            return is_leap_year(args.get("year"))
        if name == "getCwd":
            return get_cwd()
        if name == "listDirectory":
            return list_directory(args.get("path"))
        if name == "getFileType":
            return get_file_type(args.get("path"))
        if name == "readFile":
            return read_file(args.get("path"), args.get("max_chars", 4000))
        if name == "createFile":
            return create_file(
                args.get("directory", "."),
                args.get("filename"),
                args.get("content", ""),
                bool(args.get("overwrite", False)),
            )
        if name == "editFile":
            return edit_file(
                args.get("path"),
                args.get("mode", "overwrite"),
                args.get("content", ""),
                args.get("old_str"),
                args.get("new_str"),
            )
        if name == "createDirectory":
            return create_directory(args.get("path"))
        if name == "deleteDirectory":
            return delete_directory(args.get("path"))
        if name == "deleteFile":
            return delete_file(args.get("path"))
        if name == "renamePath":
            return rename_path(args.get("old_path"), args.get("new_path"))
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
    print(" 流式多轮聊天（isLeapYear + 本地文件操作工具）")
    print(f" 模型: {MODEL} | 接口: {CHAT_URL}")
    print(f" 工作目录: {os.getcwd()}")
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
