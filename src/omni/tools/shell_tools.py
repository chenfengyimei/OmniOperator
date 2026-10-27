"""本地命令执行工具集（toolset=shell）：agent 跑命令的入口（Hermes 同款能力）。

安全设计：
- run_command 一律 CONFIRM：CLI 问人 / Web 横幅确认 / QQ 触发从严（策略在 Approver）；
- peek_command（SAFE）：只读白名单（进程/端口/文件查看/git 只读/设备列表），
  agent 免确认做观察；白名单外一律引导走 run_command（需确认）；
- 双保险：超时（默认 30s，上限 300s）+ 输出截断（保尾部）；
- 解码按字节捕获后 utf-8 → gbk 逐级尝试（中文 Windows 的 netstat/tasklist 坑）。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from ..config import PROJECT_ROOT
from .registry import Risk, Tool, ToolRegistry

MAX_OUTPUT_CHARS = 8000
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0

# 只读白名单（peek_command）：前缀匹配即放行，新增需过评审
_PEEK_RE = re.compile(
    r"^\s*("
    r"tasklist|netstat|dir|type|where|systeminfo|ipconfig|"
    r"get-content|"
    r"git (status|log|diff|branch|show|remote)|"
    r"python (--version|-V)|uv (--version|-V)|uv pip list|"
    r"adb (devices|--version)"
    r")\b",
    re.IGNORECASE,
)

# 硬性黑名单（Hermes tool_guardrails 精神）：灾难级操作，命中即拒——
# 即便用户已确认也不执行（确认可以授权"有风险"，不能授权"灾难"）。
# 清单刻意精炼为六类，避免误伤正常开发操作（git reset --hard、del 临时文件等不拦）：
_HARD_BLOCKED_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # 1. 递归强删根/用户目录（POSIX rm 删除独立的 / 或 ~；Windows 盘根整删）
        r"\brm\s+(?:-\w+\s+)*(?:--\s+)?(?<![\w./~-])(?:/|~)(?=\s|$)",
        r"\b(rd|del|rmdir)\s+/s\s+/q\s+[a-z]:\\?\s*$",
        r"remove-item\s+.*-recurse.*-force\s+[a-z]:\\\s*$",
        # 2. 裸盘 / 文件系统 / 分区
        r"\bdd\b[^|;]*\bof=/dev/",
        r"\bmkfs\b|\bwipefs\b|\bfdisk\b|\bdiskpart\b|\bformat\s+[a-z]:",
        # 3. fork bomb（bash 与 cmd 形态）
        r":\(\)\s*\{[^}]*\};*:",
        r"%0\s*\|\s*%0",
        # 4. 远程内容直接进 shell 执行
        r"(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|fi|da)?sh\b",
        r"\b(iex|invoke-expression)\b[^|]*\b(iwr|invoke-webrequest|curl|new-object\s+system\.net)",
        # 5. 主机电源控制
        r"\b(shutdown|poweroff|halt)\b(\.exe)?\s",
        # 6. SSH 私钥外读
        r"(type|get-content|cat)\b[^|;]*(id_rsa|id_ed25519|id_ecdsa)\b",
    )
]

Runner = Callable[[str, float], "tuple[int, bytes, bytes]"]


def hard_blocked_reason(command: str) -> str:
    """命中硬黑名单返回拒绝理由，否则空串（唯一检查入口）。"""
    for pattern in _HARD_BLOCKED_RES:
        m = pattern.search(command)
        if m:
            return (
                f"命中硬性安全黑名单（{m.group(0)[:60]!r}）：该类操作即使获得确认也"
                "不会执行——确认可以授权'有风险'，不能授权'灾难'。请改用安全等价操作"
            )
    return ""


def decode_output(data: bytes) -> str:
    """utf-8 → gbk 逐级尝试，最后容错替换（GBK 控制台的中文不丢结构）。"""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return f"（输出过长，仅保留末尾 {MAX_OUTPUT_CHARS} 字符）\n…" + text[-MAX_OUTPUT_CHARS:]


def _format_result(code: int, out: bytes, err: bytes) -> str:
    text_out = decode_output(out).strip()
    text_err = decode_output(err).strip()
    parts = [f"退出码 {code}"]
    if text_out:
        parts.append("[输出]\n" + _clip(text_out))
    if text_err:
        parts.append("[错误输出]\n" + _clip(text_err))
    if not text_out and not text_err:
        parts.append("（无输出）")
    return "\n".join(parts)


def register_shell_tools(
    registry: ToolRegistry, *, runner: Runner | None = None, cwd: Path | None = None
) -> None:
    """runner 可注入（单测离线跑）；默认 subprocess + shell=True 于项目根执行。"""
    working_dir = cwd or PROJECT_ROOT

    def _default_runner(cmd: str, timeout_s: float) -> tuple[int, bytes, bytes]:
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            cwd=str(working_dir),
            timeout=timeout_s,
            env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    execute = runner or _default_runner

    def _run(cmd: str, timeout_s: float) -> str:
        try:
            code, out, err = execute(cmd, timeout_s)
        except subprocess.TimeoutExpired:
            return (
                f"错误：命令超时（>{timeout_s:.0f}s）已终止。"
                "把命令拆小步，或用更大的 timeout_s 重试"
            )
        except OSError as exc:
            return f"错误：命令执行失败：{exc}"
        return _format_result(code, out, err)

    def _parse_timeout(args: dict, default: float, upper: float) -> float:
        try:
            return min(max(float(args.get("timeout_s", default)), 1), upper)
        except (TypeError, ValueError):
            return default

    def run_command(args: dict) -> str:
        cmd = str(args.get("command", "")).strip()
        if not cmd:
            return "错误：command 不能为空"
        blocked = hard_blocked_reason(cmd)
        if blocked:
            return f"错误：{blocked}"
        return _run(cmd, _parse_timeout(args, DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S))

    def peek_command(args: dict) -> str:
        cmd = str(args.get("command", "")).strip()
        if not cmd:
            return "错误：command 不能为空"
        if not _PEEK_RE.match(cmd):
            return (
                "错误：该命令不在只读白名单（观察类：tasklist/netstat/dir/type/"
                "git 只读子命令/adb devices 等）；执行类操作请用 run_command（需用户确认）"
            )
        return _run(cmd, _parse_timeout(args, 15.0, 60.0))

    registry.register(
        Tool(
            name="peek_command",
            description=(
                "执行只读观察类命令（免确认）：tasklist/netstat/dir/type/ipconfig/"
                "git status|log|diff/adb devices 等。仅用于观察，不做任何修改"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "只读命令"},
                    "timeout_s": {"type": "number", "description": "超时秒数（默认 15，上限 60）"},
                },
                "required": ["command"],
            },
            func=peek_command,
            toolset="shell",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="run_command",
            description=(
                "在本机执行任意命令（需用户确认；在项目根目录运行）。"
                "用于构建/测试/安装/脚本等操作；危险与花钱操作会被审批拦下。"
                "输出过长时只保留末尾部分"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout_s": {"type": "number", "description": "超时秒数（默认 30，上限 300）"},
                },
                "required": ["command"],
            },
            func=run_command,
            toolset="shell",
            risk=Risk.CONFIRM,
        )
    )
