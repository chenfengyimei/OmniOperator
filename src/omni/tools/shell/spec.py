"""SAFE 命令解析：把命令串解析成**受控 argv**，解析不出来就拒绝。

为什么整体重写（A01/A02 的根因）：
- 旧实现用前缀正则匹配后再交给 `shell=True`，只约束了开头几个词，
  后半段完全自由（`git status & echo x`、`tasklist > f`、`git branch <name>`）；
- 现在先拒绝一切 shell 元字符，再按词表解析"已知命令 + 有限参数"，
  未知形态一律拒绝——没有 shell 参与，就不存在"前缀合法、后半段任意"的空间；
- `dir` 用 Python 实现（不调 cmd 内建），`type`/`get-content` 移出 SAFE：
  读任意文件内容属于文件能力域（D13）的职责，SAFE 观察不该开这个口子；
- 可执行文件解析到系统目录或绝对路径，避免工作区里放一个同名 exe 劫持。

明确不做：不解释通配符、不做变量展开、不支持管道/重定向/命令串联。
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..pathpolicy import PROTECTED_DIR_NAMES, PROTECTED_FILE_GLOBS

# shell 元字符：出现即拒绝（没有 shell，就没有"复合命令"这回事）
_SHELL_METACHARS = {
    "&": "命令串联/后台执行",
    "|": "管道",
    ";": "命令串联",
    ">": "重定向",
    "<": "重定向",
    "^": "转义/续行",
    "`": "命令替换",
    "\n": "多行命令",
    "\r": "多行命令",
    "\x00": "空字节",
}
_DOLLAR_PAREN = re.compile(r"\$\(")

# 敏感路径名单走 tools/pathpolicy.py 的单一实现：文件工具与 SAFE 观察必须同一边界，
# 否则两边各加一条漏一条，就成了"换个入口照样读到密钥"（A02）。
_PROTECTED_DIR_NAMES = PROTECTED_DIR_NAMES
_PROTECTED_FILE_GLOBS = PROTECTED_FILE_GLOBS

_GIT_GLOBAL_DENY_PREFIXES = (
    "-c",
    "-C",
    "--config-env",
    "--exec-path",
    "--upload-pack",
    "--receive-pack",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
)
# 这些选项能让 git 调用外部程序或写文件：即使是只读子命令也必须拒绝
_GIT_DENY_ANYWHERE = (
    "--ext-diff",
    "--textconv",
    "--no-index",
    "--output",
    "--open-files-in-pager",
    "-O",
)

_REV_RE = re.compile(r"^[A-Za-z0-9_./~^@{}-]+$")          # 无冒号：`HEAD:.env` 会打印已提交内容
_INT_RE = re.compile(r"^\d{1,4}$")
_PATHSPEC_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


@dataclass(frozen=True)
class SafeCommand:
    """解析通过的 SAFE 调用：直接交给 argv 执行器，不经 shell。"""

    argv: list[str]
    label: str  # 面向模型的可读形式（不含临时拼接过的东西）


@dataclass(frozen=True)
class Rejected:
    reason: str


def _reject(reason: str) -> Rejected:
    return Rejected(reason)


# ---------------------------------------------------------------- 词法解析


def _tokenize(command: str) -> list[str] | Rejected:
    """按空白切词，支持双引号分组；引号不配对、出现元字符都算拒绝。"""
    tokens: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in command:
        if ch in _SHELL_METACHARS:
            return _reject(
                f"命令含 shell 元字符 {ch!r}（{_SHELL_METACHARS[ch]}）："
                "只读观察不接受复合命令、管道或重定向"
            )
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if in_quote:
        return _reject("命令里的双引号没有配对")
    if buf:
        tokens.append("".join(buf))
    if not tokens:
        return _reject("命令为空")
    if _DOLLAR_PAREN.search(command):
        return _reject("命令含 $() 命令替换")
    return tokens


# ---------------------------------------------------------------- 可执行文件


def _system_root_dirs() -> list[Path]:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    base = Path(root)
    return [base / "System32", base / "Sysnative", base]


def _resolve_system_exe(name: str, root: Path) -> Path | Rejected:
    """只信任系统目录里的同名可执行文件（防工作区/PATH 劫持）。"""
    candidates = [d / f"{name}.exe" for d in _system_root_dirs()]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    found = shutil.which(name)
    if not found:
        return _reject(f"找不到系统命令 {name}")
    resolved = Path(found).resolve()
    if _is_within(resolved, root):
        return _reject(f"拒绝执行工作区内的同名可执行文件：{resolved}")
    return resolved


def _resolve_path_exe(name: str, root: Path) -> Path | Rejected:
    """PATH 上的第三方工具（git/adb）：必须是绝对路径且不在工作区内。"""
    found = shutil.which(name)
    if not found:
        return _reject(f"找不到命令 {name}（未安装或不在 PATH）")
    resolved = Path(found).resolve()
    if _is_within(resolved, root):
        return _reject(f"拒绝执行工作区内的同名可执行文件：{resolved}")
    return resolved


# ---------------------------------------------------------------- 路径守卫


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_protected_name(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in _PROTECTED_FILE_GLOBS)


def _protected_reason(path: Path, root: Path) -> str:
    """命中敏感文件/目录返回理由，否则空串。"""
    if is_protected_name(path.name):
        return f"{path.name} 属于密钥/凭据/个人数据类文件，SAFE 观察不读取"
    parts = {p.lower() for p in path.parts}
    for name in _PROTECTED_DIR_NAMES:
        if name in parts:
            return f"路径包含受保护目录 {name}/（密钥、个人数据或浏览器登录态）"
    if not _is_within(path, root):
        return (
            "路径在工作区之外：SAFE 观察只在项目根内读文件系统，"
            "其它位置请用 run_command（需人工确认）"
        )
    return ""


# ---------------------------------------------------------------- 各命令规格


def _spec_dir(tokens: list[str], root: Path) -> SafeCommand | Rejected:
    if len(tokens) > 2:
        return _reject("dir 只接受一个可选路径参数")
    target = root if len(tokens) == 1 else (root / tokens[1])
    try:
        resolved = target.resolve()
    except OSError as exc:
        return _reject(f"路径无法解析：{exc}")
    reason = _protected_reason(resolved, root)
    if reason:
        return _reject(reason)
    return SafeCommand(argv=["__dir__", str(resolved)], label=f"dir {tokens[1] if len(tokens) > 1 else '.'}")


_TASKLIST_FLAGS = {"/V", "/SVC", "/NH", "/APPS"}
_TASKLIST_FILTER_RE = re.compile(r"^[A-Za-z0-9 _.()=<>!*+-]{1,120}$")


def _spec_tasklist(tokens: list[str]) -> SafeCommand | Rejected:
    args = tokens[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        upper = arg.upper()
        if upper in _TASKLIST_FLAGS:
            i += 1
            continue
        if upper == "/FO":
            if i + 1 >= len(args) or args[i + 1].upper() not in {"TABLE", "LIST", "CSV"}:
                return _reject("tasklist /FO 只接受 TABLE|LIST|CSV")
            i += 2
            continue
        if upper == "/FI":
            if i + 1 >= len(args) or not _TASKLIST_FILTER_RE.match(args[i + 1]):
                return _reject("tasklist /FI 的过滤表达式不合法")
            i += 2
            continue
        return _reject(f"tasklist 不接受参数 {arg}（只允许 /FI /FO /NH /V /SVC /APPS）")
    return SafeCommand(argv=["tasklist", *args], label=" ".join(["tasklist", *args]))


_NETSTAT_HOST_RE = re.compile(r"^-\w{1,6}$")


def _spec_netstat(tokens: list[str]) -> SafeCommand | Rejected:
    args = tokens[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if _NETSTAT_HOST_RE.match(arg):
            # 只允许观察类开关，-b 需要管理员且会暴露可执行路径
            if not set(arg[1:].lower()) <= set("anorsepqt"):
                return _reject(f"netstat 不接受开关 {arg}")
            i += 1
            continue
        if arg == "-p":
            if i + 1 >= len(args) or args[i + 1].upper() not in {
                "TCP", "UDP", "TCPV6", "UDPV6",
            }:
                return _reject("netstat -p 只接受 TCP|UDP|TCPv6|UDPv6")
            i += 2
            continue
        return _reject(f"netstat 不接受参数 {arg}")
    return SafeCommand(argv=["netstat", *args], label=" ".join(["netstat", *args]))


def _spec_where(tokens: list[str]) -> SafeCommand | Rejected:
    patterns = tokens[1:]
    if not patterns or len(patterns) > 3:
        return _reject("where 需要 1–3 个文件名模式")
    for pattern in patterns:
        if "/" in pattern or "\\" in pattern or ".." in pattern or pattern.startswith("-"):
            return _reject(f"where 只接受文件名模式，不接受路径：{pattern}")
    return SafeCommand(argv=["where", *patterns], label=" ".join(["where", *patterns]))


def _spec_ipconfig(tokens: list[str]) -> SafeCommand | Rejected:
    args = [a.lower() for a in tokens[1:]]
    if args in ([], ["/all"], ["/displaydns"]):
        return SafeCommand(argv=["ipconfig", *tokens[1:]], label=" ".join(tokens))
    return _reject("ipconfig 只允许无参数、/all 或 /displaydns（刷新类写操作不在 SAFE）")


def _spec_systeminfo(tokens: list[str]) -> SafeCommand | Rejected:
    args = tokens[1:]
    if not args:
        return SafeCommand(argv=["systeminfo"], label="systeminfo")
    if len(args) == 2 and args[0].upper() == "/FO" and args[1].upper() in {"LIST", "TABLE", "CSV"}:
        return SafeCommand(argv=["systeminfo", *args], label=" ".join(tokens))
    return _reject("systeminfo 只允许无参数或 /FO LIST|TABLE|CSV")


def _spec_adb(tokens: list[str]) -> SafeCommand | Rejected:
    args = tokens[1:]
    if args in (["devices"], ["devices", "-l"], ["--version"], ["version"]):
        return SafeCommand(argv=["adb", *args], label=" ".join(["adb", *args]))
    return _reject("adb 只允许 devices [-l] 或 --version（其它 adb 子命令属执行类）")


def _git_arg_is_denied(arg: str) -> str:
    for prefix in _GIT_GLOBAL_DENY_PREFIXES:
        if arg == prefix or (prefix.startswith("--") and arg.startswith(prefix + "=")):
            return f"全局选项 {arg} 可改变 git 的配置/执行路径"
    for bad in _GIT_DENY_ANYWHERE:
        if bad == "-O":
            if arg == "-O" or arg.startswith("-O"):
                return f"{arg} 可让 git 读外部文件"
        elif arg == bad or arg.startswith(bad + "="):
            return f"{arg} 会调用外部程序或写文件"
    return ""


def _git_status(args: list[str]) -> str:
    allowed = {
        "--short", "-s", "--porcelain", "--porcelain=v1", "--branch", "-b",
        "--no-color", "-uno", "-unormal", "-uall",
        "--untracked-files=no", "--untracked-files=normal", "--untracked-files=all",
    }
    for arg in args:
        if arg not in allowed:
            return f"git status 不接受参数 {arg}"
    return ""


def _git_log(args: list[str]) -> str:
    allowed = {
        "--oneline", "--graph", "--decorate", "--all", "--stat", "--name-only",
        "--name-status", "--no-color", "--abbrev-commit", "-n",
    }
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-n", "--max-count") or arg.startswith("--max-count="):
            if arg in ("-n", "--max-count"):
                if i + 1 >= len(args) or not _INT_RE.match(args[i + 1]):
                    return "git log -n 需要一个整数"
                i += 2
                continue
            if not _INT_RE.match(arg.split("=", 1)[1]):
                return "git log --max-count 需要一个整数"
            i += 1
            continue
        if re.fullmatch(r"-\d{1,4}", arg):
            i += 1
            continue
        if arg.startswith("--author=") or arg.startswith("--grep="):
            value = arg.split("=", 1)[1]
            if not value or len(value) > 80:
                return "git log 的过滤文本为空或过长"
            i += 1
            continue
        if arg in allowed:
            i += 1
            continue
        return f"git log 不接受参数 {arg}"
    return ""


def _git_diff_like(name: str, args: list[str]) -> str:
    allowed = {
        "--stat", "--name-only", "--name-status", "--no-color", "--cached",
        "--staged", "--compact-summary", "--summary", "--numstat", "-U",
    }
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            paths = args[i + 1:]
            if len(paths) > 5:
                return f"{name} 的路径参数过多"
            for path in paths:
                if not _PATHSPEC_RE.match(path) or ".." in path:
                    return f"{name} 不接受该路径参数 {path}"
            return ""
        if arg in allowed:
            i += 1
            continue
        if arg.startswith("--unified=") and _INT_RE.match(arg.split("=", 1)[1]):
            i += 1
            continue
        if re.fullmatch(r"-U\d{1,3}", arg):
            i += 1
            continue
        return f"{name} 不接受参数 {arg}"
    return ""


def _git_show(args: list[str]) -> str:
    allowed = {"--stat", "--name-only", "--no-color", "--oneline", "--summary"}
    for arg in args:
        if arg in allowed:
            continue
        if _REV_RE.match(arg) and not arg.startswith("-"):
            continue
        return f"git show 不接受参数 {arg}"
    return ""


def _git_branch(args: list[str]) -> str:
    allowed = {
        "-a", "-r", "-v", "-vv", "--list", "--show-current", "--merged",
        "--no-merged", "--all", "--verbose", "--no-color",
    }
    for arg in args:
        if arg not in allowed:
            # 位置参数就是创建/删除/重命名分支——正是 A02 的绕过点
            return f"git branch 只允许列举（{arg} 会创建或修改分支）"
    return ""


def _git_remote(args: list[str]) -> str:
    for arg in args:
        if arg not in ("-v", "--verbose"):
            return f"git remote 只允许列举（{arg} 会修改远端配置）"
    return ""


_GIT_SUBCOMMANDS = {
    "status": _git_status,
    "log": _git_log,
    "diff": lambda args: _git_diff_like("git diff", args),
    "show": _git_show,
    "branch": _git_branch,
    "remote": _git_remote,
}


def _spec_git(tokens: list[str], root: Path) -> SafeCommand | Rejected:
    args = tokens[1:]
    if not args:
        return _reject("git 需要子命令（只读子命令：status/log/diff/show/branch/remote）")
    for arg in args:
        reason = _git_arg_is_denied(arg)
        if reason:
            return _reject(reason)
    sub = args[0]
    validator = _GIT_SUBCOMMANDS.get(sub)
    if validator is None:
        return _reject(
            f"git {sub} 不在只读白名单（只允许 status/log/diff/show/branch/remote 的列举形态）"
        )
    rest = args[1:]
    problem = validator(rest)
    if problem:
        return _reject(problem)
    exe = _resolve_path_exe("git", root)
    if isinstance(exe, Rejected):
        return exe
    return SafeCommand(argv=[str(exe), *args], label=" ".join(["git", *args]))


def _spec_system(name: str, builder):
    def spec(tokens: list[str], root: Path) -> SafeCommand | Rejected:
        result = builder(tokens)
        if isinstance(result, Rejected):
            return result
        exe = _resolve_system_exe(name, root)
        if isinstance(exe, Rejected):
            return exe
        return SafeCommand(argv=[str(exe), *result.argv[1:]], label=result.label)

    return spec


def _spec_adb_wrapped(tokens: list[str], root: Path) -> SafeCommand | Rejected:
    result = _spec_adb(tokens)
    if isinstance(result, Rejected):
        return result
    exe = _resolve_path_exe("adb", root)
    if isinstance(exe, Rejected):
        return exe
    return SafeCommand(argv=[str(exe), *result.argv[1:]], label=result.label)


_SPECS = {
    "dir": _spec_dir,
    "tasklist": _spec_system("tasklist", _spec_tasklist),
    "netstat": _spec_system("netstat", _spec_netstat),
    "where": _spec_system("where", _spec_where),
    "ipconfig": _spec_system("ipconfig", _spec_ipconfig),
    "systeminfo": _spec_system("systeminfo", _spec_systeminfo),
}

SAFE_COMMANDS = tuple(sorted([*_SPECS, "git", "adb"]))


def parse_safe_command(command: str, *, root: Path) -> SafeCommand | Rejected:
    """SAFE 命令的唯一入口：解析成功才可能执行，失败必须带上可定位的理由。"""
    tokens = _tokenize((command or "").strip())
    if isinstance(tokens, Rejected):
        return tokens
    name = tokens[0].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    try:
        root = root.resolve()
    except OSError:
        return _reject("工作目录无法解析")
    if name == "git":
        return _spec_git(tokens, root)
    if name == "adb":
        return _spec_adb_wrapped(tokens, root)
    spec = _SPECS.get(name)
    if spec is None:
        return _reject(
            f"{name} 不在只读白名单（可观察：{', '.join(SAFE_COMMANDS)}）；"
            "执行类操作请用 run_command（需用户确认）"
        )
    return spec(tokens, root)
