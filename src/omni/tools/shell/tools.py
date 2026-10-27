"""shell 工具集（toolset=shell）：peek_command（SAFE 观察）与 run_command（CONFIRM 执行）。

职责边界：
- 本文件只做"工具契约 + 结果呈现"；命令解析在 `spec.py`，进程边界在 `process.py`，
  灾难级黑名单在 `danger.py`；
- SAFE 观察走 argv + shell=False + 最小环境；任意命令保留 shell 语义但同样受
  超时、输出上限和进程树回收约束；
- 拒绝理由必须可定位（哪一条规则、哪个参数），不回显敏感内容。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...config import PROJECT_ROOT
from ..registry import Risk, Tool, ToolRegistry
from .danger import hard_blocked_reason
from .process import (
    MAX_READ_BYTES,
    ProcResult,
    minimal_env,
    run_argv,
    run_shell,
    user_env,
)
from .spec import (
    SAFE_COMMANDS,
    Rejected,
    SafeCommand,
    is_protected_name,
    parse_safe_command,
)

MAX_OUTPUT_CHARS = 8000
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0
SAFE_DEFAULT_TIMEOUT_S = 15.0
SAFE_MAX_TIMEOUT_S = 60.0
MAX_DIR_ENTRIES = 200

SafeRunner = Callable[[list[str], float], ProcResult]
ShellRunner = Callable[[str, float], ProcResult]


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
    if len(out) >= MAX_READ_BYTES or len(err) >= MAX_READ_BYTES:
        parts.append(f"（输出已达 {MAX_READ_BYTES // 1024} KiB 读取上限，后续内容未保留）")
    return "\n".join(parts)


def _timeout_text(timeout_s: float, reclaimed: bool, *, safe: bool) -> str:
    """超时话术：区分"确认回收"与"可能还在跑"，且不把未知副作用说成可重试。"""
    head = f"错误：命令超时（>{timeout_s:.0f}s）"
    if reclaimed:
        if safe:
            return head + "。已确认终止整棵进程树，可缩小范围重试"
        return (
            head + "。已确认终止整棵进程树，但该命令可能已产生副作用（写文件/提交/发请求），"
            "重试前先核对实际结果，不要盲目重复执行"
        )
    if safe:
        return head + "，且进程树回收状态未知（可能有残留进程）。先核查残留再重试，不要把超时当成没执行"
    return (
        head + "，且进程树回收状态未知：副作用未知。请用户核查残留进程与实际结果，不要自动重试"
    )


def _list_dir(path: Path) -> str:
    """`dir` 的受控实现：只列名称/类型/大小，遮蔽密钥类条目，有条数上限。"""
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        return f"错误：无法列出目录：{exc}"
    lines = [f"目录 {path}（共 {len(entries)} 项，按名称排序）"]
    hidden = 0
    shown = 0
    for entry in entries:
        if is_protected_name(entry.name):
            hidden += 1
            continue
        if shown >= MAX_DIR_ENTRIES:
            lines.append(f"…（已截断，只列前 {MAX_DIR_ENTRIES} 项）")
            break
        try:
            is_dir = entry.is_dir()
            size = 0 if is_dir else entry.stat().st_size
        except OSError:
            is_dir, size = False, 0
        kind = "[目录]" if is_dir else "[文件]"
        size_text = "" if is_dir else f"{size:>10} B"
        lines.append(f"  {kind} {size_text}  {entry.name}")
        shown += 1
    if hidden:
        lines.append(f"（另有 {hidden} 项密钥/个人数据类条目未列出）")
    return "\n".join(lines)


def register_shell_tools(
    registry: ToolRegistry,
    *,
    runner: SafeRunner | None = None,
    shell_runner: ShellRunner | None = None,
    cwd: Path | None = None,
    root: Path | None = None,
) -> None:
    """runner/shell_runner 可注入（单测离线跑，不碰真实命令）。

    root 是 SAFE 观察的文件系统边界（默认与工作目录一致）；run_command 不受它限制，
    它本来就等价于"用户在终端里敲命令"，由人工确认把关。
    """
    working_dir = Path(cwd or PROJECT_ROOT)
    safe_root = Path(root or working_dir)

    def _default_safe(argv: list[str], timeout_s: float) -> ProcResult:
        return run_argv(argv, timeout_s, working_dir, minimal_env())

    def _default_shell(command: str, timeout_s: float) -> ProcResult:
        return run_shell(command, timeout_s, working_dir, user_env())

    execute_safe = runner or _default_safe
    execute_shell = shell_runner or _default_shell

    def _parse_timeout(args: dict, default: float, upper: float) -> float:
        try:
            return min(max(float(args.get("timeout_s", default)), 1), upper)
        except (TypeError, ValueError):
            return default

    def _dispatch_safe(parsed: SafeCommand, timeout_s: float) -> str:
        if parsed.argv[0] == "__dir__":
            return _list_dir(Path(parsed.argv[1]))
        result = execute_safe(list(parsed.argv), timeout_s)
        if result.timed_out:
            return _timeout_text(timeout_s, result.reclaimed, safe=True)
        return _format_result(result.code, result.stdout, result.stderr)

    def peek_command(args: dict) -> str:
        command = str(args.get("command", "")).strip()
        if not command:
            return "错误：command 不能为空"
        parsed = parse_safe_command(command, root=safe_root)
        if isinstance(parsed, Rejected):
            return f"错误：{parsed.reason}"
        return _dispatch_safe(parsed, _parse_timeout(args, SAFE_DEFAULT_TIMEOUT_S, SAFE_MAX_TIMEOUT_S))

    def run_command(args: dict) -> str:
        command = str(args.get("command", "")).strip()
        if not command:
            return "错误：command 不能为空"
        blocked = hard_blocked_reason(command)
        if blocked:
            return f"错误：{blocked}"
        timeout_s = _parse_timeout(args, DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S)
        result = execute_shell(command, timeout_s)
        if result.timed_out:
            return _timeout_text(timeout_s, result.reclaimed, safe=False)
        return _format_result(result.code, result.stdout, result.stderr)

    registry.register(
        Tool(
            name="peek_command",
            description=(
                "执行只读观察类命令（免确认）：dir/tasklist/netstat/ipconfig/where/systeminfo/"
                "git status|log|diff|show|branch（列举）/remote（列举）/adb devices。"
                "不接受复合命令、管道、重定向；只允许项目根内的路径；不读密钥与个人数据。"
                "其它操作请改用 run_command（需用户确认）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            f"单条只读命令（可用：{', '.join(SAFE_COMMANDS)}）；"
                            "不要用 & | ; > < 等符号"
                        ),
                    },
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
            requires_human=True,
        )
    )
