"""工作区路径边界（D13）：规范化、根校验、敏感项与"检查后不变"。

为什么不是 `Path.resolve()` 一行了事：
- Windows 上 `C:\\a\\..\\..\\Windows` 这种写法在不存在的路径上不会被 `resolve` 拦下来，
  必须**先拒绝可疑形态，再解析，再按根校验**；
- junction / symlink 可以把工作区里的一个目录指到任意位置，所以判断必须在
  `realpath`（解析链接）之后做，而不是看字符串前缀；
- `file.txt:stream` 是 NTFS 备用数据流，读它等于读附加在文件上的隐藏内容；
- `\\\\host\\share`、`\\\\?\\C:\\`、`C:foo`（驱动器相对）都不是"工作区路径"。

模型侧没有"再加一个根"的参数：允许的根只来自配置（用户显式写下的额外目录），
否则模型自己写一句 `path="C:/Users"` 就等于给自己扩权。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from ..pathpolicy import protected_reason

MAX_PATH_CHARS = 4096
_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PathRejected(ValueError):
    """路径越界/敏感/形态可疑：调用方应把它当成"没做任何事"回给模型。"""

    def __init__(self, reason: str, *, code: str = "path_rejected") -> None:
        self.reason = reason
        self.code = code
        super().__init__(reason)


@dataclass(frozen=True)
class Root:
    """一个被允许的根目录；`deny_prefixes` 是这个根下的相对禁区。"""

    path: Path
    label: str
    deny_prefixes: tuple[str, ...] = ()
    write_deny_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolved:
    """规范化后的路径 + 它相对哪个根。`rel` 用 `/` 分隔，便于展示与匹配。"""

    path: Path
    rel: str
    root: Root
    exists: bool
    is_dir: bool
    is_symlink: bool = False

    def display(self) -> str:
        """给人看的路径：根内用相对路径，额外根用 `<标签>/相对路径`。"""
        return self.rel if self.root.label == "工作区" else f"{self.root.label}/{self.rel}"


@dataclass
class Workspace:
    """允许读写的根集合（构造只在 bootstrap，路径参数永不参与）。"""

    root: Path
    extra_roots: list[Path] = field(default_factory=list)
    deny_globs: tuple[str, ...] = ()
    write_deny_globs: tuple[str, ...] = ()
    read_only_roots: list[Path] = field(default_factory=list)
    _roots: list[Root] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self) -> None:
        base = _canonical(self.root)
        roots = [
            Root(
                path=base,
                label="工作区",
                deny_prefixes=("data",),
                # 配置、技能库、运行数据都有专门的写入通道（校验 + 版本 + 额度），
                # 用文本工具直接覆盖等于绕过那一整套。
                write_deny_prefixes=("data", "skills", "config", ".git"),
            )
        ]
        for extra in self.extra_roots:
            path = _canonical(extra)
            if path == base or any(item.path == path for item in roots):
                continue
            roots.append(
                Root(
                    path=path,
                    label=f"授权目录{len(roots)}",
                    deny_prefixes=(),
                    write_deny_prefixes=(),
                )
            )
        self._roots = roots

    # ---------------------------------------------------------------- 查询

    @property
    def roots(self) -> list[Root]:
        return list(self._roots)

    def describe(self) -> str:
        """给工具描述与错误信息用的一句话（不含用户目录细节，只列标签）。"""
        extra = len(self._roots) - 1
        return f"工作区（{len(self._roots)} 个允许根，其中额外授权 {extra} 个）"

    # ---------------------------------------------------------------- 解析

    def assert_unchanged(self, resolved: Resolved) -> None:
        """确认"检查过的路径"此刻仍解析到同一个位置。

        防的是：校验时 `ws/sub` 是普通目录，写入前它被换成了指向工作区外的 junction——
        解析结果会变，于是写入会落到根外。这不是完整的防护（真正的原子语义要文件系统支持），
        但把窗口从"整个回合"缩到"一次系统调用"。
        """
        current = _canonical(resolved.path)
        if os.path.normcase(str(current)) != os.path.normcase(str(resolved.path)):
            raise PathRejected(
                "路径在检查之后发生了变化（父目录可能被换成了链接），本次放弃操作",
                code="path_changed",
            )

    def resolve(
        self, raw: str, *, must_exist: bool = False, for_write: bool = False
    ) -> Resolved:
        text = _clean(raw)
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = self._roots[0].path / text
        canonical = _canonical(candidate)
        root, rel = self._locate(canonical)
        exists = canonical.exists()
        is_dir = canonical.is_dir() if exists else False
        is_symlink = candidate.is_symlink()
        resolved = Resolved(
            path=canonical,
            rel=rel,
            root=root,
            exists=exists,
            is_dir=is_dir,
            is_symlink=is_symlink,
        )
        self._check_policy(resolved, must_exist=must_exist, for_write=for_write)
        return resolved

    def _locate(self, canonical: Path) -> tuple[Root, str]:
        for root in sorted(self._roots,key=lambda item:len(item.path.parts),reverse=True):
            rel = _relative_under(root.path, canonical)
            if rel is not None:
                return root, rel
        raise PathRejected(
            "路径不在允许的根内：默认只在工作区内读写。需要其它目录请让用户在"
            " config.yaml 的 files.extra_roots 里显式授权（本工具不接受自填根目录）",
            code="path_outside_root",
        )

    def _check_policy(
        self, resolved: Resolved, *, must_exist: bool, for_write: bool
    ) -> None:
        rel = resolved.rel
        parts = tuple(part for part in rel.split("/") if part)
        reason = protected_reason(parts, parts[-1] if parts else "")
        if not reason:
            reason = self._prefix_reason(rel, resolved.root.deny_prefixes, "不可读")
        if not reason:
            reason = _glob_reason(rel, self.deny_globs, "不可读")
        if reason:
            raise PathRejected(reason, code="sensitive_path")

        if for_write:
            if any(resolved.root.path==_canonical(path) for path in self.read_only_roots):
                raise PathRejected('资料目录对模型只读；请用户通过文件管理添加资料',code='read_only_root')
            if resolved.is_symlink:
                raise PathRejected(
                    "目标是符号链接/快捷方式：拒绝顺着链接写文件（可能改到工作区外）",
                    code="write_through_link",
                )
            reason = self._prefix_reason(
                rel, resolved.root.write_deny_prefixes, "不可写"
            )
            if not reason:
                reason = _glob_reason(rel, self.write_deny_globs, "不可写")
            if reason:
                raise PathRejected(reason, code="write_protected")
            if resolved.exists and resolved.is_dir:
                raise PathRejected("目标是目录，不能当作文件写入", code="is_directory")

        if must_exist and not resolved.exists:
            raise PathRejected(f"{resolved.display()} 不存在", code="not_found")

    @staticmethod
    def _prefix_reason(rel: str, prefixes: tuple[str, ...], verb: str) -> str:
        lowered = rel.casefold()
        for prefix in prefixes:
            if lowered == prefix or lowered.startswith(prefix.rstrip("/") + "/"):
                return (
                    f"{prefix}/ 属于{verb}区域：这里有专门的读写通道"
                    "（运行数据/技能版本/配置校验），文本工具不能绕过"
                )
        return ""


# ---------------------------------------------------------------- 形态检查


def _clean(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise PathRejected("路径不能为空", code="empty_path")
    if len(text) > MAX_PATH_CHARS:
        raise PathRejected(f"路径过长（>{MAX_PATH_CHARS} 字符）", code="path_too_long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise PathRejected("路径包含控制字符/空字节", code="bad_path")
    if text.startswith("\\\\") or text.startswith("//"):
        raise PathRejected(
            "拒绝 UNC/设备路径（\\\\host\\share、\\\\?\\C:\\）：只能访问本机允许的根",
            code="unc_path",
        )
    drive = _DRIVE_RE.match(text)
    body = text[2:] if drive else text
    if drive and body and body[0] not in "\\/":
        raise PathRejected(
            f"拒绝驱动器相对路径 {text!r}（缺分隔符，会落到该盘的当前目录）",
            code="drive_relative",
        )
    if ":" in body:
        raise PathRejected(
            "路径含多余的冒号（NTFS 备用数据流或驱动器相对路径）", code="ads_or_drive_relative"
        )
    for segment in re.split(r"[\\/]+", body):
        if not segment or segment in {".", ".."}:
            continue  # 相对段的落点由后面的根校验决定
        if segment != segment.rstrip(" ."):
            raise PathRejected(
                f"路径段 {segment!r} 以空格或点结尾（Windows 会静默改写，易被绕过）",
                code="bad_segment",
            )
        stem = segment.split(".", 1)[0].lower()
        if stem in _RESERVED_NAMES:
            raise PathRejected(f"路径段 {segment!r} 是 Windows 保留设备名", code="reserved_name")
    return text


def _canonical(path: Path) -> Path:
    """解析链接/junction 后的真实路径；不存在的尾部保留原样。

    `strict=False` 不够：Python 的 `ntpath.realpath` 对不存在路径同样会解析已存在的
    前缀，这正是我们要的——`link_to_outside/secret.txt` 即使文件不存在也要落到根外。
    """
    return Path(os.path.realpath(str(path)))


def _relative_under(root: Path, target: Path) -> str | None:
    """`target` 在 `root` 内则返回 posix 相对路径（大小写不敏感），否则 None。"""
    root_s = os.path.normcase(str(root)).rstrip("\\/")
    target_s = os.path.normcase(str(target))
    if target_s == root_s:
        return "."
    if not target_s.startswith(root_s + os.sep):
        return None
    rel = os.path.relpath(str(target), str(root))
    return rel.replace("\\", "/")


def _glob_reason(rel: str, globs: tuple[str, ...], verb: str) -> str:
    lowered = rel.casefold()
    for pattern in globs:
        if fnmatch(lowered, pattern.casefold()):
            return f"{rel} 命中{verb}规则 {pattern}"
    return ""
