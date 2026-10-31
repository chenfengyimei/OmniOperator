"""结构化文件工具（D13，toolset=files）。

能力刻意分成"读"和"写"两组：
- 读（list/search/read/diff/history）是 SAFE：不产生副作用，但**边界在路径层**，
  越界、敏感、UNC、ADS、链接逃逸一律在打开文件前拒绝；
- 写（write/restore）是 CONFIRM：必须人工确认，且要求 `expected_hash` 等于当前内容指纹，
  否则拒绝覆盖——这条防的不是模型恶意，而是"用户刚在编辑器里改过、模型拿着旧内容盖上"。

回读校验：写完立刻按字节重新读一遍比对指纹。磁盘满、被杀软拦截、被云盘同步改回去，
都会在这里暴露成"写入未通过回读校验"，而不是一句"已保存"。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from ...system.atomic import atomic_write_bytes
from ...system.filelock import FileLock, LockTimeout
from ..pathpolicy import is_protected_name, protected_dir
from ..registry import Risk, Tool, ToolRegistry
from ..result import SIDE_EFFECT_APPLIED, SIDE_EFFECT_UNKNOWN, ToolResult
from . import io as fio
from .history import FileHistory, SnapshotTooLarge
from .paths import PathRejected, Resolved, Workspace
from .search import SKIP_DIR_NAMES, BadQuery, SearchOutcome, compile_query, search_tree

MAX_FILE_BYTES = 4 * 1024 * 1024      # 可读全文/可备份的上限
MAX_WRITE_BYTES = 2 * 1024 * 1024     # 单次写入上限
MAX_DIFF_LINES = 200
WRITE_DIFF_LINES = 60
LOCK_TIMEOUT_S = 5.0


def register_file_tools(
    registry: ToolRegistry,
    workspace: Workspace,
    history: FileHistory,
    *,
    lock_dir: Path,
    max_read_lines: int = 200,
    max_list_entries: int = 200,
    max_search_files: int = 2000,
    max_search_results: int = 80,
    max_search_seconds: float = 5.0,
) -> None:
    # ------------------------------------------------------------ 读：列目录

    def files_list(args: dict[str, Any]) -> str:
        try:
            target = workspace.resolve(str(args.get("path", ".")), must_exist=True)
        except PathRejected as exc:
            return f"错误：{exc}"
        if not target.is_dir:
            return f"错误：{target.display()} 不是目录（读文件内容用 files_read）"
        limit = _int(args.get("limit"), max_list_entries, 1, 1000)
        entries: list[tuple[str, str]] = []
        hidden = 0
        junk = 0
        junk = 0
        try:
            with os.scandir(target.path) as iterator:
                for entry in iterator:
                    name = entry.name
                    try:
                        if entry.is_symlink():
                            entries.append((name, "link"))
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if _sensitive_dir(name, target.rel):
                                hidden += 1
                                continue
                            if name.lower() in SKIP_DIR_NAMES:
                                junk += 1
                                continue
                            if name.lower() in SKIP_DIR_NAMES:
                                junk += 1
                                continue
                            entries.append((name + "/", "dir"))
                        else:
                            if _sensitive_file(name, target.rel):
                                hidden += 1
                                continue
                            entries.append((name, _size_label(entry.stat().st_size)))
                    except OSError:
                        continue
        except OSError as exc:
            return f"错误：无法列出 {target.display()}（{exc}）"
        entries.sort(key=lambda item: (item[1] != "dir", item[0].lower()))
        shown = entries[:limit]
        head = f"{target.display()}（{len(entries)} 项"
        head += f"，显示前 {len(shown)} 项）" if len(entries) > len(shown) else "）"
        lines = [head]
        for name, kind in shown:
            if kind == "dir":
                lines.append(f"  {name}")
            elif kind == "link":
                lines.append(f"  {name}    →（链接，未展开）")
            else:
                lines.append(f"  {name}    {kind}")
        if hidden:
            lines.append(f"（已隐藏 {hidden} 个敏感项：密钥/凭据/运行数据不在可读范围）")
        if junk:
            lines.append(f"（已跳过 {junk} 个缓存/构建目录）")
        if junk:
            lines.append(f"（已跳过 {junk} 个缓存/构建目录）")
        if len(entries) > len(shown):
            lines.append(f"（还有 {len(entries) - len(shown)} 项未显示，可调大 limit）")
        return "\n".join(lines)

    # ------------------------------------------------------------ 读：搜索

    def files_search(args: dict[str, Any]) -> str:
        query = str(args.get("query", ""))
        try:
            pattern = compile_query(
                query,
                regex=bool(args.get("regex", False)),
                case_sensitive=bool(args.get("case_sensitive", False)),
            )
            target = workspace.resolve(str(args.get("path", ".")), must_exist=True)
        except (BadQuery, PathRejected) as exc:
            return f"错误：{exc}"
        if not target.is_dir:
            return f"错误：{target.display()} 不是目录（搜索以目录为范围）"
        outcome = search_tree(
            target.path,
            target.rel,
            pattern,
            glob=str(args.get("glob") or "*"),
            max_files=max_search_files,
            max_results=_int(args.get("limit"), max_search_results, 1, 500),
            time_budget_s=max_search_seconds,
        )
        head = (
            f"在 {target.display()} 下搜索 {query!r}："
            f"扫描 {outcome.scanned} 个文本文件，命中 {len(outcome.matches)} 处"
        )
        lines = [head]
        for hit in outcome.matches:
            lines.append(f"{hit.rel}:{hit.line}: {hit.snippet}")
        if not outcome.matches:
            lines.append("（没有命中）")
        lines.append(_search_notes(outcome).strip())
        return "\n".join(line for line in lines if line)

    # ------------------------------------------------------------ 读：文件

    def files_read(args: dict[str, Any]) -> str:
        try:
            target = workspace.resolve(str(args.get("path", "")), must_exist=True)
        except PathRejected as exc:
            return f"错误：{exc}"
        if target.is_dir:
            return f"错误：{target.display()} 是目录（列目录用 files_list）"
        offset = _int(args.get("offset"), 1, 1, 10_000_000)
        limit = _int(args.get("limit"), max_read_lines, 1, 2000)
        try:
            window = fio.read_window(target.path, offset=offset, limit=limit)
        except PathRejected as exc:
            return f"错误：{exc}"
        except OSError as exc:
            return f"错误：读取 {target.display()} 失败（{exc}）"
        if window.total is not None and offset > window.total and window.total > 0:
            return f"错误：{target.display()} 只有 {window.total} 行，offset={offset} 超出范围"
        total = (
            f"共 {window.total} 行" if window.total is not None else "总行数未知（未读到末尾）"
        )
        fingerprint = (
            f" · 指纹 {window.fingerprint[:12]}" if window.fingerprint else ""
        )
        head = (
            f"{target.display()}（{window.encoding}{' +BOM' if window.bom else ''}"
            f" · {_size_label(window.size)} · {total} · "
            f"显示第 {offset}-{offset + max(len(window.lines) - 1, 0)} 行{fingerprint}）"
        )
        lines = [head]
        width = len(str(offset + max(len(window.lines) - 1, 0)))
        for index, text in enumerate(window.lines, start=offset):
            lines.append(f"{index:>{width}}│ {text}")
        if not window.lines:
            lines.append("（这一范围没有内容）")
        if window.more:
            lines.append(f"（还有后续内容，继续读请用 offset={offset + len(window.lines)}）")
        if window.clipped_lines:
            lines.append(f"（{window.clipped_lines} 行过长已截断）")
        if not window.fingerprint:
            lines.append("（文件较大，未计算指纹：修改前请用 files_diff 确认当前内容）")
        return "\n".join(lines)

    # ------------------------------------------------------------ 读：预览差异

    def files_diff(args: dict[str, Any]) -> str:
        mode = str(args.get("mode", "overwrite"))
        try:
            target = workspace.resolve(str(args.get("path", "")), for_write=True)
            current, raw = _read_current(target)
            new_text = _compose(current, args, mode)
            new_bytes = _encode_with(new_text, raw)
        except (PathRejected, ValueError) as exc:
            return f"错误：{exc}"
        if len(new_bytes) > MAX_WRITE_BYTES:
            return f"错误：写入内容 {len(new_bytes)} 字节，超过上限 {MAX_WRITE_BYTES} 字节"
        if raw is not None and new_text == current:
            return f"{target.display()} 内容没有变化，无需写入。"
        diff, total = fio.unified_diff(
            current,
            new_text,
            name=target.display(),
            max_lines=MAX_DIFF_LINES,
        )
        head = (
            f"{'新文件' if raw is None else '修改'} {target.display()}："
            f"{len(new_bytes)} 字节，写入后指纹 {fio.sha256_hex(new_bytes)[:12]}"
        )
        note = "" if raw is not None else "\n（文件当前不存在：写入用 mode=create）"
        return f"{head}{note}\n{diff or '（只有编码/行尾变化）'}\n（差异共 {total} 行）"

    # ------------------------------------------------------------ 写

    def files_write(args: dict[str, Any]) -> ToolResult:
        mode = str(args.get("mode", "overwrite"))
        try:
            target = workspace.resolve(str(args.get("path", "")), for_write=True)
        except PathRejected as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="path_rejected")
        parent = target.path.parent
        if not parent.is_dir():
            if not bool(args.get("create_dirs", False)):
                return ToolResult.fail(
                    f"错误：父目录 {parent.name}/ 不存在。确实需要新建目录时传 create_dirs=true",
                    error_code="parent_dir_missing",
                )
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return ToolResult.fail(f"错误：无法创建目录（{exc}）", error_code="mkdir_failed")
        try:
            with _file_lock(lock_dir, target):
                return _write_locked(target, args, mode)
        except LockTimeout as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="lock_timeout")

    def _write_locked(target: Resolved, args: dict[str, Any], mode: str) -> ToolResult:
        """写入的每条退出路径都带精确状态：可能已落盘的回读/落点分支必须是 unknown。"""
        try:
            current, raw = _read_current(target)
        except (PathRejected, OSError) as exc:
            return ToolResult.fail(f"错误：读取 {target.display()} 失败（{exc}）", error_code="read_failed")
        existed = raw is not None
        if mode == "create" and existed:
            return ToolResult.fail(
                f"错误：{target.display()} 已存在（create 只用于新建）。"
                "改已有文件请用 overwrite，并带上当前指纹",
                error_code="target_exists",
            )
        if mode in {"overwrite", "replace"} and not existed:
            return ToolResult.fail(
                f"错误：{target.display()} 不存在（overwrite 不创建新文件）。"
                "新建请用 mode=create",
                error_code="target_missing",
            )
        current_hash = fio.sha256_hex(raw) if raw is not None else ""
        if existed:
            expected = str(args.get("expected_hash", "") or "").strip()
            if not expected:
                return ToolResult.fail(
                    "错误：缺少 expected_hash。写入前先用 files_read 或 files_diff 取得当前指纹"
                    f"（当前为 {current_hash[:12]}），确认内容没变后再写入",
                    error_code="expected_hash_missing",
                )
            if not fio.hash_matches(expected, current_hash):
                return ToolResult.fail(
                    f"错误：内容已被修改，拒绝覆盖。当前指纹 {current_hash[:12]}，"
                    f"与你给的 {expected[:12]} 不一致。请重新读取后再改——本次没有写入任何内容",
                    error_code="hash_conflict",
                )
        try:
            new_text = _compose(current, args, mode)
            new_bytes = _encode_with(new_text, raw)
        except ValueError as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="invalid_content")
        if len(new_bytes) > MAX_WRITE_BYTES:
            return ToolResult.fail(
                f"错误：写入内容 {len(new_bytes)} 字节，超过上限 {MAX_WRITE_BYTES} 字节",
                error_code="too_large",
            )
        if existed and new_text == current:
            return ToolResult.ok(
                f"{target.display()} 内容没有变化，未写入（指纹 {current_hash[:12]}）。"
            )

        # 先备份再写：备份失败就不写，避免"改了但回不去"
        backup_note = "（原文件不存在，无需备份）"
        if raw is not None:
            try:
                reason = str(args.get("summary", "") or "").strip()
                snap = history.commit(
                    target.display(),
                    raw,
                    summary=(f"{reason}（改前）" if reason else "写入前自动备份"),
                )
                backup_note = f"备份 rev {snap.rev}"
            except SnapshotTooLarge as exc:
                return ToolResult.fail(f"错误：{exc}", error_code="backup_too_large")
            except OSError as exc:
                return ToolResult.fail(
                    f"错误：写入前备份失败，本次没有修改文件（{exc}）", error_code="backup_failed"
                )
        try:
            workspace.assert_unchanged(target)  # 落盘前最后确认一次路径没被换掉
            atomic_write_bytes(target.path, new_bytes)
        except PathRejected as exc:
            return ToolResult.fail(
                f"错误：{exc}；原文件未改动，{backup_note}", error_code="path_changed"
            )
        except OSError as exc:
            # atomic_write_bytes 失败时删临时文件且不动原文件（system/atomic 契约）
            return ToolResult.fail(
                f"错误：写入失败（{exc}）；原文件未改动，{backup_note}", error_code="write_failed"
            )
        # 回读校验：指纹不一致说明有别的进程/同步盘插了一脚
        try:
            written = target.path.read_bytes()
        except OSError as exc:
            return ToolResult.fail(
                f"错误：写入后回读失败（{exc}）；{backup_note}，请重新读取确认",
                error_code="readback_failed",
                side_effect_state=SIDE_EFFECT_UNKNOWN,  # 文件可能已写入
            )
        new_hash = fio.sha256_hex(new_bytes)
        try:
            workspace.assert_unchanged(target)
        except PathRejected as exc:
            return ToolResult.fail(
                f"错误：写入过程中路径被改变（{exc}）。文件内容已是新内容，"
                f"{backup_note}；请重新读取确认落点",
                error_code="path_changed_after_write",
                side_effect_state=SIDE_EFFECT_UNKNOWN,  # 内容已落盘，落点不确定
            )
        if fio.sha256_hex(written) != new_hash:
            return ToolResult.fail(
                f"错误：写入后回读校验不通过（磁盘内容与预期不一致）。{backup_note}；"
                "请重新读取确认，不要假设已经保存成功",
                error_code="readback_mismatch",
                side_effect_state=SIDE_EFFECT_UNKNOWN,  # 磁盘上已有别的写入
            )
        diff, total = fio.unified_diff(
            current, new_text, name=target.display(), max_lines=WRITE_DIFF_LINES
        )
        head = (
            f"已写入 {target.display()}：{len(new_bytes)} 字节，指纹 {new_hash[:12]}"
            f"（{backup_note}，回读校验通过）"
        )
        return ToolResult.ok(
            f"{head}\n{diff or '（仅编码/行尾变化）'}\n（差异共 {total} 行）",
            side_effect_state=SIDE_EFFECT_APPLIED,
            verification=f"回读校验通过，指纹 {new_hash[:12]}",
        )

    # ------------------------------------------------------------ 历史与回滚

    def files_history(args: dict[str, Any]) -> str:
        try:
            target = workspace.resolve(str(args.get("path", "")), for_write=True)
        except PathRejected as exc:
            return f"错误：{exc}"
        items = history.list(target.display(), limit=20)
        if not items:
            return f"{target.display()} 还没有备份记录（第一次写入时会自动生成）。"
        return f"{target.display()} 的备份（新→旧）：\n" + "\n".join(
            f"  {item.brief()}" for item in items
        )

    def files_restore(args: dict[str, Any]) -> ToolResult:
        try:
            target = workspace.resolve(str(args.get("path", "")), for_write=True)
        except PathRejected as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="path_rejected")
        rev = _int(args.get("rev"), 0, 1, 1_000_000)
        payload = history.read(target.display(), rev)
        if payload is None:
            return ToolResult.fail(
                f"错误：{target.display()} 没有 rev {rev} 的备份（用 files_history 查看）",
                error_code="rev_missing",
            )
        try:
            with _file_lock(lock_dir, target):
                _current, raw = _read_current(target)
                current_hash = fio.sha256_hex(raw) if raw is not None else ""
                expected = str(args.get("expected_hash", "") or "").strip()
                if raw is not None and not fio.hash_matches(expected, current_hash):
                    return ToolResult.fail(
                        f"错误：内容已被修改，拒绝回滚。当前指纹 {current_hash[:12]}，"
                        f"你给的 {expected[:12] or '（未提供）'} 不一致；本次没有写入",
                        error_code="hash_conflict",
                    )
                if raw is not None:
                    history.commit(target.display(), raw, summary=f"回滚到 rev {rev} 前的自动备份")
                workspace.assert_unchanged(target)
                atomic_write_bytes(target.path, payload)
                written = target.path.read_bytes()
        except LockTimeout as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="lock_timeout")
        except (PathRejected, OSError, SnapshotTooLarge) as exc:
            # 文案本身就在对冲"如已改动"：按副作用未知处理，不假设没有写入
            return ToolResult.fail(
                f"错误：回滚失败（{exc}）；如已改动，可再用 files_history 找上一版",
                error_code="restore_failed",
                side_effect_state=SIDE_EFFECT_UNKNOWN,
            )
        if fio.sha256_hex(written) != fio.sha256_hex(payload):
            return ToolResult.fail(
                "错误：回滚后回读校验不通过，请重新读取确认（不要假设已恢复）",
                error_code="readback_mismatch",
                side_effect_state=SIDE_EFFECT_UNKNOWN,
            )
        return ToolResult.ok(
            f"已把 {target.display()} 恢复到 rev {rev}"
            f"（{len(payload)} 字节，指纹 {fio.sha256_hex(payload)[:12]}，回读校验通过）",
            side_effect_state=SIDE_EFFECT_APPLIED,
            verification=f"回读校验通过，指纹 {fio.sha256_hex(payload)[:12]}",
        )

    # ------------------------------------------------------------ 注册

    registry.register(
        Tool(
            name="files_list",
            description="列出工作区某个目录的内容（目录优先，敏感项自动隐藏）",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的路径，默认 ."},
                    "limit": {"type": "integer", "description": "最多显示多少项"},
                },
            },
            func=files_list,
            toolset="files",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="files_search",
            description="在工作区内按内容搜索文本文件（有界；敏感文件与二进制自动跳过）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要搜索的文字或正则"},
                    "path": {"type": "string", "description": "搜索起点目录，默认 ."},
                    "glob": {"type": "string", "description": "文件名过滤，如 *.py"},
                    "regex": {"type": "boolean", "description": "把 query 当正则（默认按字面量）"},
                    "case_sensitive": {"type": "boolean", "description": "区分大小写"},
                    "limit": {"type": "integer", "description": "最多返回多少条命中"},
                },
                "required": ["query"],
            },
            func=files_search,
            toolset="files",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="files_read",
            description="按行读取工作区内的文本文件（带行号、编码、总行数与内容指纹；二进制拒绝）",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "offset": {"type": "integer", "description": "起始行号（1 起）"},
                    "limit": {"type": "integer", "description": "最多读多少行"},
                },
                "required": ["path"],
            },
            func=files_read,
            toolset="files",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="files_diff",
            description="预览一次写入的差异（不落盘）：先看这里，再决定是否 files_write",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {
                        "type": "string",
                        "description": "写入后的内容（overwrite）或替换文本（replace）",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "create", "replace"],
                        "description": "overwrite=整文件覆盖；create=新建；replace=替换唯一片段",
                    },
                    "old_text": {"type": "string", "description": "mode=replace 时要被替换的原文"},
                },
                "required": ["path", "content"],
            },
            func=files_diff,
            toolset="files",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="files_write",
            description=(
                "写入/修改工作区文本文件（需人工确认）：必须带当前内容指纹 expected_hash，"
                "写前自动备份、写后回读校验"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {
                        "type": "string",
                        "description": "写入后的内容（overwrite/create）或替换文本（replace）",
                    },
                    "expected_hash": {
                        "type": "string",
                        "description": "当前内容指纹（files_read/files_diff 返回，取前 8 位即可）；create 模式不需要",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "create", "replace"],
                        "description": "overwrite=整文件覆盖；create=新建（已存在则拒绝）；replace=替换唯一片段",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "mode=replace 时要被替换的原文（必须唯一出现）",
                    },
                    "create_dirs": {
                        "type": "boolean",
                        "description": "父目录不存在时是否创建",
                    },
                    "summary": {
                        "type": "string",
                        "description": "这次改动的目的（写进备份记录，便于回滚时辨认）",
                    },
                },
                "required": ["path", "content"],
            },
            func=files_write,
            toolset="files",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="files_history",
            description="查看某个文件的写入备份（可回滚的历史版本）",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
            func=files_history,
            toolset="files",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="files_restore",
            description="把文件恢复到某个备份版本（需人工确认；同样要求当前指纹）",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "rev": {
                        "type": "integer",
                        "description": "备份版本号（files_history 里看到的 rev）",
                    },
                    "expected_hash": {
                        "type": "string",
                        "description": "当前内容指纹，防止覆盖别人的修改",
                    },
                },
                "required": ["path", "rev", "expected_hash"],
            },
            func=files_restore,
            toolset="files",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )


# ---------------------------------------------------------------- 辅助


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def _size_label(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _rel_parts(base_rel: str) -> list[str]:
    return [p for p in str(base_rel or "").split("/") if p and p != "."]


def _sensitive_dir(name: str, base_rel: str) -> bool:
    return bool(protected_dir([*_rel_parts(base_rel), name]))


def _sensitive_file(name: str, base_rel: str) -> bool:
    return bool(protected_dir(_rel_parts(base_rel))) or is_protected_name(name)


def _search_notes(outcome: SearchOutcome) -> str:
    notes: list[str] = []
    if outcome.hits_truncated:
        notes.append("命中已达上限，结果被截断（用更精确的 query 或调大 limit）")
    if outcome.files_truncated:
        notes.append(f"文件数达到上限 {outcome.visited}，扫描被截断")
    if outcome.timed_out:
        notes.append("达到时间上限，扫描被截断")
    if outcome.skipped_sensitive:
        notes.append(f"跳过 {outcome.skipped_sensitive} 个敏感文件/目录，不显示名字与内容")
    if outcome.skipped_binary:
        notes.append(f"跳过 {outcome.skipped_binary} 个二进制文件")
    if outcome.skipped_large:
        notes.append(f"跳过 {outcome.skipped_large} 个超大文件")
    if outcome.skipped_links:
        notes.append(f"跳过 {outcome.skipped_links} 个符号链接")
    if outcome.errors:
        notes.append(f"{outcome.errors} 个路径无法访问")
    return ("（" + "；".join(notes) + "）") if notes else ""


def _read_current(target: Resolved) -> tuple[str, bytes | None]:
    """返回 (规范化换行后的文本, 原始字节)；不存在时返回 ("", None)。"""
    if not target.exists:
        return "", None
    if target.is_dir:
        raise PathRejected("目标是目录", code="is_directory")
    data = fio.read_bytes(target.path, limit=MAX_FILE_BYTES)
    if fio.is_binary(data):
        raise PathRejected(
            f"{target.display()} 是二进制文件，文本工具不修改它", code="binary_file"
        )
    return fio.decode_bytes(data).text, data


def _encode_with(text: str, raw: bytes | None) -> bytes:
    """按目标文件现有编码/换行/BOM 编码；新文件用 UTF-8 + LF。"""
    if raw is not None:
        decoded = fio.decode_bytes(raw)
        return fio.encode_text(
            text, encoding=decoded.encoding, bom=decoded.bom, newline=decoded.newline
        )
    return fio.encode_text(text, encoding="utf-8", bom=False, newline="\n")


def _compose(current: str, args: dict[str, Any], mode: str) -> str:
    content = args.get("content", "")
    text = str(content if content is not None else "")
    if "\x00" in text:
        raise ValueError("内容包含空字节：文本工具不写二进制（请用专门的方式处理二进制）")
    if mode in {"overwrite", "create"}:
        return text
    if mode == "replace":
        old_text = str(args.get("old_text", "") or "")
        if not old_text:
            raise ValueError("mode=replace 需要 old_text（要被替换的原文）")
        occurrences = current.count(old_text)
        if occurrences != 1:
            raise ValueError(
                f"要替换的原文出现 {occurrences} 次（必须唯一）：请带上更完整的上下文"
            )
        return current.replace(old_text, text)
    raise ValueError(f"未知 mode {mode!r}（支持 overwrite/create/replace）")


def _file_lock(lock_dir: Path, target: Resolved) -> FileLock:
    """每个目标文件一把跨进程锁：读→比对→写 必须在同一把锁里完成。"""
    key = hashlib.sha1(target.path.as_posix().casefold().encode("utf-8")).hexdigest()[:20]
    return FileLock(
        Path(lock_dir) / f"{key}.lock",
        timeout_s=LOCK_TIMEOUT_S,
        owner=f"pid:{os.getpid()}:files",
    )
