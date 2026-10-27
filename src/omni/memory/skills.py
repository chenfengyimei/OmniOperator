"""技能存储（程序性记忆）：skills/<name>/SKILL.md，agentskills.io 兼容子集。

技能 = "怎么做某类事"的可复用流程——事实/档案回答"知道什么"，技能回答
"知道怎么做"（Hermes 验证的自进化载体，见 xuexi/NOTES.md §1.4）。

纪律：
- 渐进披露：list 只给名字+描述（省 token），view 才展开全文；
- 结构固定：frontmatter(name/description/version/platforms) + 正文
  （When to Use / Procedure / Pitfalls / Verification 四节）；
- 轻量 linter 守门：名字格式、描述 ≤60 字、正文 50–24000 字，
  防止"事故日志式"堆砌与一句话碎片；
- 文件式存储，git 可版本化；类级锁防止主线程与 review 线程并发建同名。
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from ..config import SKILLS_DIR

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
MAX_DESCRIPTION = 60
MIN_BODY = 50
MAX_BODY = 24000
PLATFORMS = ["windows", "macos"]
SECTIONS = ("When to Use", "Procedure", "Pitfalls", "Verification")
REFERENCE_SUFFIXES = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv"}
MAX_REFERENCE_BYTES = 64 * 1024
MAX_REFERENCES = 60


def parse_skill_md(path: Path) -> dict[str, Any] | None:
    """解析 SKILL.md → {"meta": {...}, "body": "..."}；格式不对返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
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


class SkillStore:
    _create_lock = threading.Lock()  # 类级锁：主实例与 review 实例共用

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root or SKILLS_DIR).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

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
            return skill_md.read_text(encoding="utf-8")
        target = self._reference_path(name, path)
        if target is None:
            raise ValueError("只允许技能 references/ 内的文本文件；禁止绝对路径、越界与链接")
        if not target.is_file():
            return None
        with target.open("rb") as stream:
            data = stream.read(MAX_REFERENCE_BYTES + 1)
        if len(data) > MAX_REFERENCE_BYTES:
            raise ValueError("参考文件超过 64 KiB，请拆分后按需读取")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("参考文件必须是 UTF-8 文本") from exc
        if "\x00" in text:
            raise ValueError("参考文件包含二进制内容")
        return text

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

    def create(self, name: str, description: str, body: str) -> tuple[bool, str]:
        """创建技能（linter 守门）；失败信息自包含，回传给调用方自行调整。"""
        if not NAME_RE.match((name or "").strip()):
            return False, (
                "错误：技能名需为 3-40 位小写字母/数字/连字符"
                "（如 bilibili-watch），且不以连字符开头/结尾"
            )
        name = name.strip()
        description = (description or "").strip()
        if not description or len(description) > MAX_DESCRIPTION:
            return False, (
                f"错误：description 必须非空且 ≤{MAX_DESCRIPTION} 字（当前 {len(description)}）"
            )
        body = (body or "").strip()
        if len(body) < MIN_BODY:
            return False, f"错误：正文太短（<{MIN_BODY} 字），技能应是完整流程而非一句话"
        if len(body) > MAX_BODY:
            return False, f"错误：正文超过 {MAX_BODY} 字，请精简或拆分成多个技能"
        with self._create_lock:
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
            path.write_text(content, encoding="utf-8")
        return True, f"已创建技能 {name}（skills/{name}/SKILL.md）"

    def patch(self, name: str, section: str, content: str) -> tuple[bool, str]:
        """按小节增量修改（Hermes：patch 优先于 rewrite，省 token）。

        只重写四节之一（When to Use / Procedure / Pitfalls / Verification），
        其余原文保留；版本号 +1。典型用途：踩坑后往 Pitfalls 补一条。
        """
        path = self._safe_path(name)
        if path is None or not path.exists():
            return False, f"错误：技能 {name or '（空）'} 不存在（先 skills_list 查看）"
        parsed = parse_skill_md(path)
        if parsed is None:
            return False, "错误：SKILL.md 格式损坏，请用 skill_rewrite 全文重写"
        section = (section or "").strip()
        if section not in SECTIONS:
            return False, f"错误：section 必须是 {' / '.join(SECTIONS)} 之一"
        content = (content or "").strip()
        if not content:
            return False, "错误：content 不能为空"
        body = str(parsed["body"])
        pattern = re.compile(
            rf"(?ms)^## {re.escape(section)}\s*\n(.*?)(?=^## |\Z)"
        )
        matched = pattern.search(body)
        if matched is None:
            return False, f"错误：正文里没有 {section} 小节（四节为固定结构）"
        new_body = (
            body[: matched.start(1)] + content + "\n\n" + body[matched.end(1) :]
        ).strip() + "\n"
        if len(new_body) > MAX_BODY:
            return False, f"错误：修改后正文超过 {MAX_BODY} 字，请精简内容"
        return self._write_versioned(path, dict(parsed["meta"]), new_body)

    def rewrite(self, name: str, description: str, body: str) -> tuple[bool, str]:
        """全文重写（patch 装不下结构级变化时用）；描述与正文重新过 linter。"""
        path = self._safe_path(name)
        if path is None or not path.exists():
            return False, f"错误：技能 {name or '（空）'} 不存在（先 skills_list 查看）"
        parsed = parse_skill_md(path)
        if parsed is None:
            return False, "错误：SKILL.md 格式损坏，无法解析 frontmatter"
        description = (description or "").strip()
        body = (body or "").strip()
        if not description or len(description) > MAX_DESCRIPTION:
            return False, (
                f"错误：description 必须非空且 ≤{MAX_DESCRIPTION} 字"
                f"（当前 {len(description)}）"
            )
        if len(body) < MIN_BODY:
            return False, f"错误：正文太短（<{MIN_BODY} 字），技能应是完整流程"
        if len(body) > MAX_BODY:
            return False, f"错误：正文超过 {MAX_BODY} 字，请精简或拆分"
        meta = dict(parsed["meta"])
        meta["description"] = description
        return self._write_versioned(path, meta, body + "\n")

    def _write_versioned(
        self, path: Path, meta: dict[str, Any], body: str
    ) -> tuple[bool, str]:
        """统一落盘：版本号 +1（修正也要留痕），类级锁防并发写。"""
        try:
            meta["version"] = int(meta.get("version", 1)) + 1
        except (TypeError, ValueError):
            meta["version"] = 1
        content = (
            "---\n"
            + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            + "---\n\n"
            + body.rstrip()
            + "\n"
        )
        with self._create_lock:
            path.write_text(content, encoding="utf-8")
        name = str(meta.get("name") or path.parent.name)
        return True, f"已更新技能 {name}（v{meta['version']}）"

    def _safe_path(self, name: str) -> Path | None:
        if not NAME_RE.match((name or "").strip()):
            return None
        path = self._root / name.strip() / "SKILL.md"
        return path if self._contained(path) else None
