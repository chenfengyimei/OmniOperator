"""技能存储（程序性记忆）：skills/<name>/SKILL.md，agentskills.io 兼容子集。

技能 = "怎么做某类事"的可复用流程——事实/档案回答"知道什么"，技能回答
"知道怎么做"（Hermes 验证的自进化载体，见 xuexi/NOTES.md §1.4）。

纪律：
- 渐进披露：list 只给名字+描述（省 token），view 才展开全文；
- 结构固定：frontmatter(name/description/version/platforms) + 正文
  （When to Use / Procedure / Pitfalls / Verification 四节）；
- 轻量 linter 守门：名字格式、描述 ≤60 字、正文 50–24000 字，
  防止"事故日志式"堆砌与一句话碎片；
- **完整读改写跨进程互斥 + 内容寻址的历史快照**（D08）：写入前拿文件锁，
  写入走唯一临时文件 + 原子替换，保存前后两份内容，回滚产生新修订。
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import threading
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator

import yaml

from ..config import SKILLS_DIR
from ..system.atomic import atomic_write_text
from ..system.filelock import FileLock, LockTimeout
from .revisions import Revision, RevisionError, RevisionStore, reference_drift

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
MAX_DESCRIPTION = 60
MIN_BODY = 50
MAX_BODY = 24000
PLATFORMS = ["windows", "macos"]
SECTIONS = ("When to Use", "Procedure", "Pitfalls", "Verification")
REFERENCE_SUFFIXES = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv"}
MAX_REFERENCE_BYTES = 64 * 1024
MAX_REFERENCES = 60
MIN_HASH_PREFIX = 8  # 允许模型只回传哈希前缀（完整 sha256 太长，容易被截断）


def parse_skill_text(text: str) -> dict[str, Any] | None:
    """解析 SKILL.md 文本 → {"meta": {...}, "body": "..."}；格式不对返回 None。"""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    return {"meta": meta, "body": parts[2].strip()}


def parse_skill_md(path: Path) -> dict[str, Any] | None:
    """解析 SKILL.md 文件 → {"meta": {...}, "body": "..."}；读不到或格式不对返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return parse_skill_text(text)


def _digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def hash_matches(expected: str | None, actual: str) -> bool:
    """`expected` 可以是完整 sha256，也可以是 ≥8 位前缀（大小写不敏感）。"""
    want = (expected or "").strip().lower()
    if len(want) < MIN_HASH_PREFIX:
        return False
    return actual.lower().startswith(want)


class SkillStore:
    def __init__(
        self,
        root: Path | None = None,
        *,
        history_root: Path | None = None,
        owner: str = "",
        lock_timeout_s: float = 8.0,
        history_keep: int = 50,
    ) -> None:
        self._root = Path(root or SKILLS_DIR).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        # 历史默认落在技能根下的隐藏目录（临时实例自带历史，不会打到真机 data/）；
        # 真机由 bootstrap 注入 data/history，和常驻记忆共用一份修订存储。
        self._history = RevisionStore(
            Path(history_root) if history_root else self._root / ".history",
            keep=history_keep,
        )
        self._owner = owner or f"pid:{os.getpid()}"
        self._lock_timeout = float(lock_timeout_s)
        # 本进程内"上次看到的内容指纹"：模型 view 过、之后的 patch 拿它做 CAS，
        # 用户在这之间用编辑器改过文件就会冲突，而不是静默覆盖。
        self._seen: dict[str, str] = {}
        self._seen_lock = threading.Lock()

    # ---------------------------------------------------------------- 读

    def list(self) -> list[dict[str, Any]]:
        """渐进披露第一级：只给名字/描述/版本。"""
        out: list[dict[str, Any]] = []
        for skill_md in sorted(self._root.glob("*/SKILL.md")):
            if self._safe_path(skill_md.parent.name) is None:
                continue
            parsed = parse_skill_md(skill_md)
            if parsed is None:
                continue
            meta = parsed["meta"]
            out.append(
                {
                    "name": str(meta.get("name") or skill_md.parent.name),
                    "description": str(meta.get("description", "")),
                    "version": str(meta.get("version", "1")),
                }
            )
        return out

    def view(self, name: str, path: str | None = None) -> str | None:
        """第二级读 SKILL.md，第三级只读指定 references 文本；绝不执行附件。"""
        skill_md = self._safe_path(name)
        if skill_md is None or not skill_md.is_file():
            return None
        if path is None:
            data = skill_md.read_bytes()  # 指纹按字节算：CRLF 文件也要和写路径一致
            self._remember(name.strip(), _digest(data))
            return data.decode("utf-8")
        target = self._reference_path(name, path)
        if target is None:
            raise ValueError("只允许技能 references/ 内的文本文件；禁止绝对路径、越界与链接")
        if not target.is_file():
            return None
        data = self._read_reference_bytes(target)
        if data is None:
            raise ValueError("参考文件超过 64 KiB，请拆分后按需读取")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("参考文件必须是 UTF-8 文本") from exc
        if "\x00" in text:
            raise ValueError("参考文件包含二进制内容")
        return text

    def read(self, name: str) -> tuple[str, str] | None:
        """原文 + 内容指纹（写操作可用它做 expected_hash）。"""
        skill_md = self._safe_path(name)
        if skill_md is None or not skill_md.is_file():
            return None
        try:
            data = skill_md.read_bytes()
        except OSError:
            return None
        digest = _digest(data)
        self._remember(name.strip(), digest)
        return data.decode("utf-8"), digest

    def references(self, name: str) -> list[str]:
        """只列文件名，最多 60 个、四级目录；不遍历链接或读取附件正文。"""
        skill_md = self._safe_path(name)
        if skill_md is None or not skill_md.is_file():
            return []
        root = skill_md.parent / "references"
        if not self._contained(root) or not root.is_dir():
            return []
        out: list[str] = []
        visited = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            visited += 1
            if visited > 256:
                break
            parent = Path(directory)
            dirs[:] = sorted(d for d in dirs if self._contained(parent / d))
            if len(parent.relative_to(root).parts) >= 3:
                dirs.clear()
            for filename in sorted(files):
                relative = (parent / filename).relative_to(skill_md.parent).as_posix()
                if self._reference_path(name, relative) is not None:
                    out.append(relative)
                    if len(out) >= MAX_REFERENCES:
                        return out
        return out

    def _reference_path(self, name: str, relative: str) -> Path | None:
        # 同时检查 Windows 和 POSIX 语法，不能靠宿主 OS 的 Path 判断盘符与 ADS。
        parts = relative.replace("\\", "/").split("/")
        if (
            not parts or parts[0] != "references" or len(parts) < 2
            or PureWindowsPath(relative).drive
            or any(p in {"", ".", ".."} or p.endswith((" ", ".")) or ":" in p or "\x00" in p
                   or PureWindowsPath(p).is_reserved() for p in parts)
        ):
            return None
        skill_md = self._safe_path(name)
        if skill_md is None:
            return None
        target = skill_md.parent.joinpath(*parts)
        if target.suffix.lower() not in REFERENCE_SUFFIXES or not self._contained(target):
            return None
        return target

    def _read_reference_bytes(self, target: Path) -> bytes | None:
        """有界读取：超过 64 KiB 返回 None（不把大文件整个读进内存）。"""
        try:
            with target.open("rb") as stream:
                data = stream.read(MAX_REFERENCE_BYTES + 1)
        except OSError:
            return None
        return None if len(data) > MAX_REFERENCE_BYTES else data

    def _contained(self, path: Path) -> bool:
        """禁止符号链接/junction（含指向其它技能）和根目录外路径。"""
        try:
            path.resolve().relative_to(self._root)
            relative = path.relative_to(self._root)
            current = self._root
            for part in relative.parts:
                current /= part
                if current.is_symlink() or current.is_junction():
                    return False
            return True
        except (OSError, ValueError, RuntimeError):
            return False

    def exists(self, name: str) -> bool:
        path = self._safe_path(name)
        return path is not None and path.exists()

    # ---------------------------------------------------------------- 历史

    @property
    def history_root(self) -> Path:
        return self._history.root

    def _key(self, name: str) -> str:
        return f"skills/{name.strip()}"

    def history(self, name: str, limit: int | None = None) -> list[Revision]:
        try:
            return self._history.list(self._key(name), limit=limit)
        except RevisionError:
            return []

    def revision(self, name: str, rev: int) -> tuple[Revision, str] | None:
        try:
            return self._history.read(self._key(name), rev)
        except RevisionError:
            return None

    def reference_drift(self, name: str, rev: int) -> list[str]:
        """这一版保存时的附件清单与现在是否一致（附件不属于主文修订，但必须能说清）。"""
        found = self.revision(name, rev)
        if found is None:
            return []
        return reference_drift(found[0].references, self._refs_manifest(name))

    # ---------------------------------------------------------------- 写（互斥 + CAS + 版号）

    def create(
        self,
        name: str,
        description: str,
        body: str,
        *,
        author: str = "agent",
        source: str = "skill_create",
    ) -> tuple[bool, str]:
        """创建技能（linter 守门）；失败信息自包含，回传给调用方自行调整。"""
        name = (name or "").strip()
        if not NAME_RE.match(name):
            return False, (
                "错误：技能名需为 3-40 位小写字母/数字/连字符"
                "（如 bilibili-watch），且不以连字符开头/结尾"
            )
        description = (description or "").strip()
        if not description or len(description) > MAX_DESCRIPTION:
            return False, (
                f"错误：description 必须非空且 ≤{MAX_DESCRIPTION} 字（当前 {len(description)}）"
            )
        body = (body or "").strip()
        problem = self._body_problem(body, minimum=True)
        if problem:
            return False, problem
        try:
            with self._locked(name):
                if self.exists(name):
                    return False, (
                        f"错误：技能 {name} 已存在。先 skill_view 查看现有内容，"
                        "把新经验合并进去而不是建重复技能"
                    )
                path = self._safe_path(name)
                if path is None:
                    return False, "错误：技能目录包含链接或越界路径"
                path.parent.mkdir(parents=True, exist_ok=True)
                frontmatter = {
                    "name": name,
                    "description": description,
                    "version": 1,
                    "platforms": PLATFORMS,
                }
                content = (
                    "---\n"
                    + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
                    + "---\n\n"
                    + body
                    + "\n"
                )
                rev = self._commit(path, name, content, 1, author=author, source=source, summary="创建")
        except _Abort as exc:
            return False, f"错误：{exc}"
        return True, f"已创建技能 {name}（skills/{name}/SKILL.md，修订 rev {rev.rev}）"

    def patch(
        self,
        name: str,
        section: str,
        content: str,
        *,
        expected_hash: str | None = None,
        author: str = "agent",
        source: str = "skill_patch",
    ) -> tuple[bool, str]:
        """按小节增量修改（Hermes：patch 优先于 rewrite，省 token）。

        只重写四节之一（When to Use / Procedure / Pitfalls / Verification），
        其余原文保留；版本号 +1。典型用途：踩坑后往 Pitfalls 补一条。
        """
        section = (section or "").strip()
        if section not in SECTIONS:
            return False, f"错误：section 必须是 {' / '.join(SECTIONS)} 之一"
        content = (content or "").strip()
        if not content:
            return False, "错误：content 不能为空"
        name = (name or "").strip()
        try:
            with self._locked(name):
                loaded = self._load_for_write(name, expected_hash)
                if isinstance(loaded, str):
                    return False, loaded
                path, text, parsed = loaded
                body = str(parsed["body"])
                pattern = re.compile(rf"(?ms)^## {re.escape(section)}\s*\n(.*?)(?=^## |\Z)")
                matched = pattern.search(body)
                if matched is None:
                    return False, f"错误：正文里没有 {section} 小节（四节为固定结构）"
                new_body = (
                    body[: matched.start(1)] + content + "\n\n" + body[matched.end(1) :]
                ).strip() + "\n"
                if len(new_body) > MAX_BODY:
                    return False, f"错误：修改后正文超过 {MAX_BODY} 字，请精简内容"
                meta, version = self._next_meta(parsed["meta"], name)
                rev = self._commit(
                    path, name, self._render(meta, new_body), version,
                    author=author, source=source, summary=f"patch {section}",
                )
        except _Abort as exc:
            return False, f"错误：{exc}"
        return True, f"已更新技能 {name}（v{version}，rev {rev.rev}）"

    def rewrite(
        self,
        name: str,
        description: str,
        body: str,
        *,
        expected_hash: str | None = None,
        author: str = "agent",
        source: str = "skill_rewrite",
    ) -> tuple[bool, str]:
        """全文重写（patch 装不下结构级变化时用）；描述与正文重新过 linter。"""
        name = (name or "").strip()
        description = (description or "").strip()
        body = (body or "").strip()
        if not description or len(description) > MAX_DESCRIPTION:
            return False, (
                f"错误：description 必须非空且 ≤{MAX_DESCRIPTION} 字"
                f"（当前 {len(description)}）"
            )
        problem = self._body_problem(body, minimum=True)
        if problem:
            return False, problem
        try:
            with self._locked(name):
                loaded = self._load_for_write(name, expected_hash)
                if isinstance(loaded, str):
                    return False, loaded
                path, _text, parsed = loaded
                meta, version = self._next_meta(parsed["meta"], name)
                meta["description"] = description
                rev = self._commit(
                    path, name, self._render(meta, body + "\n"), version,
                    author=author, source=source, summary="全文重写",
                )
        except _Abort as exc:
            return False, f"错误：{exc}"
        return True, f"已更新技能 {name}（v{version}，rev {rev.rev}）"

    def rollback(
        self,
        name: str,
        rev: int,
        *,
        expected_hash: str | None = None,
        author: str = "agent",
        source: str = "skill_rollback",
    ) -> tuple[bool, str]:
        """回到某个历史修订：**产生新修订**，版本号继续加，不把整数改小。"""
        name = (name or "").strip()
        try:
            with self._locked(name):
                loaded = self._load_for_write(name, expected_hash)
                if isinstance(loaded, str):
                    return False, loaded
                path, _text, current = loaded
                found = self.revision(name, int(rev))
                if found is None:
                    return False, (
                        f"错误：技能 {name} 没有 rev {rev}（先 skill_history 查看可用修订）"
                    )
                old = found[0]
                restored = parse_skill_text(found[1])
                if restored is None:
                    return False, f"错误：rev {rev} 的内容格式损坏，无法作为回滚目标"
                body = str(restored["body"]).strip()
                problem = self._body_problem(body, minimum=True)
                if problem:  # 历史也可能是坏内容（用户手工写坏的），回滚前同样过 linter
                    return False, f"错误：rev {rev} 未通过校验，拒绝回滚：{problem}"
                meta, version = self._next_meta(current["meta"], name)
                meta["description"] = str(
                    restored["meta"].get("description") or meta.get("description", "")
                )
                new_rev = self._commit(
                    path, name, self._render(meta, body + "\n"), version,
                    author=author, source=source, summary=f"回滚到 rev {rev}",
                )
        except _Abort as exc:
            return False, f"错误：{exc}"
        return True, (
            f"已把技能 {name} 回滚到 rev {rev} 的内容（v{version}，"
            f"新修订 rev {new_rev.rev}；旧修订仍可再取）"
        )

    # ---------------------------------------------------------------- 写路径内部

    def _load_for_write(
        self, name: str, expected_hash: str | None
    ) -> tuple[Path, str, dict[str, Any]] | str:
        """在读改写临界区内调用：读当前内容 → 记历史 → CAS → 返回 (路径, 文本, 解析)。

        返回 `str` 表示拒绝（错误消息已成形）。
        """
        path = self._safe_path(name)
        if path is None or not path.exists():
            return f"错误：技能 {name or '（空）'} 不存在（先 skills_list 查看）"
        try:
            data = path.read_bytes()
        except OSError as exc:
            return f"错误：读取技能失败（{exc}），本次未修改"
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            return "错误：SKILL.md 不是 UTF-8 文本，请先手工修复"
        digest = _digest(data)
        parsed = parse_skill_text(text)
        # 外部编辑先落历史：即使下面因为冲突拒绝写入，用户手工改的内容也不会丢
        self._snapshot_unversioned(name, text, parsed, digest)
        expected = (expected_hash or "").strip()
        if len(expected) < MIN_HASH_PREFIX:  # 太短的前缀不当指纹（"a" 会匹配一切）
            expected = self._remembered(name)
        if expected and not hash_matches(expected, digest):
            return (
                f"错误：技能 {name} 在你读取之后被改动过（当前内容指纹 {digest[:16]}，"
                f"期望 {expected[:16]}）。请先 skill_view 查看最新内容，"
                "把改动合并进去再写；本次未修改任何内容"
            )
        if parsed is None:
            return "错误：SKILL.md 格式损坏，请用 skill_rewrite 全文重写"
        return path, text, parsed

    def _snapshot_unversioned(
        self, name: str, text: str, parsed: dict[str, Any] | None, digest: str
    ) -> None:
        """把"当前磁盘上的内容"纳入历史（如果历史里还没有这一版）。"""
        key = self._key(name)
        latest = self._history.latest(key)
        if latest is not None and latest.sha256 == digest:
            return
        version: int | None = None
        if parsed is not None:
            with contextlib.suppress(TypeError, ValueError):
                version = int(parsed["meta"].get("version", 1))
        self._history.commit(
            key,
            text,
            author="external",
            source="detected-change",
            summary="写入前检测到的内容（可能是手工编辑）",
            version=version,
            references=self._refs_manifest(name),
        )

    def _next_meta(
        self, meta: dict[str, Any], name: str
    ) -> tuple[dict[str, Any], int]:
        """版本号只增不减：无法解析的历史版本按 1 处理。"""
        current: int | None = None
        with contextlib.suppress(TypeError, ValueError):
            current = int(meta.get("version", 1))
        version = (current or 0) + 1
        next_meta = dict(meta)
        next_meta["version"] = version
        next_meta["name"] = str(next_meta.get("name") or name)
        next_meta.setdefault("platforms", list(PLATFORMS))
        return next_meta, version

    @staticmethod
    def _render(meta: dict[str, Any], body: str) -> str:
        return (
            "---\n"
            + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            + "---\n\n"
            + body.rstrip()
            + "\n"
        )

    def _commit(
        self,
        path: Path,
        name: str,
        content: str,
        version: int,
        *,
        author: str,
        source: str,
        summary: str,
    ) -> Revision:
        """原子落盘 + 记录新修订；落盘失败时原文件不变、历史也不会多一条"已生效"。"""
        try:
            atomic_write_text(path, content)
        except OSError as exc:
            raise _Abort(f"写入技能文件失败（{exc}）；原文件保持原样，本次未修改") from exc
        rev = self._history.commit(
            self._key(name),
            content,
            author=author,
            source=source,
            summary=summary,
            version=version,
            references=self._refs_manifest(name),
        )
        self._remember(name, _digest(content))
        return rev

    def _refs_manifest(self, name: str) -> dict[str, str]:
        """附件清单（文件 → sha256）：随修订一起记，之后能报"附件是否被改过"。"""
        out: dict[str, str] = {}
        for relative in self.references(name):
            target = self._reference_path(name, relative)
            if target is None:
                continue
            data = self._read_reference_bytes(target)
            if data is None:
                continue
            out[relative] = _digest(data)
        return out

    @staticmethod
    def _body_problem(body: str, *, minimum: bool) -> str:
        if len(body) > MAX_BODY:
            return f"错误：正文超过 {MAX_BODY} 字，请精简或拆分成多个技能"
        if minimum and len(body) < MIN_BODY:
            return f"错误：正文太短（<{MIN_BODY} 字），技能应是完整流程而非一句话"
        return ""

    # ---------------------------------------------------------------- 指纹与锁

    def _remember(self, name: str, digest: str) -> None:
        with self._seen_lock:
            self._seen[name] = digest

    def _remembered(self, name: str) -> str:
        with self._seen_lock:
            return self._seen.get(name, "")

    @contextlib.contextmanager
    def _locked(self, name: str) -> Iterator[None]:
        """整段读改写的跨进程互斥（锁文件在技能根的 .locks/ 下，随根共享）。"""
        if not NAME_RE.match((name or "").strip()):
            raise _Abort("技能名不合法，无法加锁")
        lock_path = self._root / ".locks" / f"{name.strip()}.lock"
        lock = FileLock(lock_path, timeout_s=self._lock_timeout, owner=self._owner)
        try:
            lock.acquire()
        except LockTimeout as exc:
            raise _Abort(str(exc)) from exc
        try:
            yield
        finally:
            lock.release()

    def _safe_path(self, name: str) -> Path | None:
        if not NAME_RE.match((name or "").strip()):
            return None
        path = self._root / name.strip() / "SKILL.md"
        return path if self._contained(path) else None


class _Abort(RuntimeError):
    """本次写入被中止（锁被占 / 落盘失败）：一律对外说"未修改"，绝不半写。"""
