"""有界目录遍历与内容搜索（D13）。

搜索是最容易越界的能力：它会把命中的**片段**带出来。所以这里：
- 敏感文件在打开之前就被跳过，既不出现路径也不出现片段（否则 `.env` 里的
  `OPENAI_API_KEY=sk-…` 会以"一行命中"的形式进模型上下文）；
- 二进制不猜编码，直接跳过；
- 文件数、结果数、耗时三重上限，防止在一个巨型目录里把回合耗死；
- 不跟随符号链接：链接可以指到工作区外，跟随就等于绕过了根校验。
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..pathpolicy import is_protected_name, protected_dir
from .io import BINARY_SNIFF_BYTES, decode_bytes, is_binary

SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "target",
        "node_modules",
        ".venv",
        "venv",
    }
)
MAX_SNIPPET_CHARS = 240


@dataclass(frozen=True)
class Match:
    rel: str
    line: int
    snippet: str


@dataclass
class SearchOutcome:
    matches: list[Match] = field(default_factory=list)
    visited: int = 0
    scanned: int = 0
    hits_truncated: bool = False
    files_truncated: bool = False
    timed_out: bool = False
    skipped_sensitive: int = 0
    skipped_binary: int = 0
    skipped_large: int = 0
    skipped_links: int = 0
    errors: int = 0


class BadQuery(ValueError):
    pass


def compile_query(query: str, *, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    text = str(query or "")
    if not text.strip():
        raise BadQuery("搜索内容不能为空")
    if len(text) > 500:
        raise BadQuery("搜索内容过长（>500 字符）")
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(text if regex else re.escape(text), flags)
    except re.error as exc:
        raise BadQuery(f"正则表达式不合法：{exc}") from exc


def search_tree(
    base: Path,
    rel_base: str,
    pattern: re.Pattern[str],
    *,
    glob: str = "*",
    max_files: int = 2000,
    max_results: int = 80,
    time_budget_s: float = 5.0,
    max_file_bytes: int = 1024 * 1024,
) -> SearchOutcome:
    """在 `base` 下做有界搜索；`rel_base` 只是展示用的相对路径前缀。"""
    outcome = SearchOutcome()
    deadline = time.monotonic() + max(float(time_budget_s), 0.1)
    # 广度优先：额度有限时先看浅层，避免第一步就扎进某个几万文件的目录
    queue: deque[tuple[Path, list[str]]] = deque([(Path(base), [])])
    while queue:
        directory, parts = queue.popleft()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name.lower())
        except OSError:
            outcome.errors += 1
            continue
        for entry in entries:
            if time.monotonic() > deadline:
                outcome.timed_out = True
                return outcome
            if outcome.visited >= max_files:
                outcome.files_truncated = True
                return outcome
            name = entry.name
            lowered = name.lower()
            try:
                if entry.is_symlink():
                    outcome.skipped_links += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if lowered in SKIP_DIR_NAMES or protected_dir([*parts, name]):
                        outcome.skipped_sensitive += 1
                        continue
                    queue.append((Path(entry.path), [*parts, name]))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                outcome.errors += 1
                continue
            outcome.visited += 1
            if is_protected_name(name) or protected_dir(parts):
                outcome.skipped_sensitive += 1
                continue
            if not fnmatch.fnmatch(lowered, glob.lower()):
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                outcome.errors += 1
                continue
            if size > max_file_bytes:
                outcome.skipped_large += 1
                continue
            try:
                with open(entry.path, "rb") as stream:
                    data = stream.read(max_file_bytes + 1)
            except OSError:
                outcome.errors += 1
                continue
            if len(data) > max_file_bytes:
                outcome.skipped_large += 1
                continue
            if is_binary(data[:BINARY_SNIFF_BYTES]):
                outcome.skipped_binary += 1
                continue
            decoded = decode_bytes(data)
            outcome.scanned += 1
            rel = "/".join([*parts, name])
            if rel_base and rel_base != ".":
                rel = f"{rel_base}/{rel}"
            for lineno, line in enumerate(decoded.text.splitlines(), start=1):
                if pattern.search(line):
                    snippet = line.strip()
                    if len(snippet) > MAX_SNIPPET_CHARS:
                        snippet = snippet[:MAX_SNIPPET_CHARS] + " …"
                    outcome.matches.append(Match(rel=rel, line=lineno, snippet=snippet))
                    if len(outcome.matches) >= max_results:
                        outcome.hits_truncated = True
                        return outcome
    return outcome
