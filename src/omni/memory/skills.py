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

import re
import threading
from pathlib import Path
from typing import Any

import yaml

from ..config import SKILLS_DIR

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
MAX_DESCRIPTION = 60
MIN_BODY = 50
MAX_BODY = 24000
PLATFORMS = ["windows", "macos"]
SECTIONS = ("When to Use", "Procedure", "Pitfalls", "Verification")


def parse_skill_md(path: Path) -> dict[str, Any] | None:
    """解析 SKILL.md → {"meta": {...}, "body": "..."}；格式不对返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
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
        self._root = root or SKILLS_DIR
        self._root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        """渐进披露第一级：只给名字/描述/版本。"""
        out: list[dict[str, Any]] = []
        for skill_md in sorted(self._root.glob("*/SKILL.md")):
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

    def view(self, name: str) -> str | None:
        """渐进披露第二级：完整 SKILL.md。"""
        path = self._safe_path(name)
        if path is None or not path.exists():
            return None
        return path.read_text(encoding="utf-8")

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
            path = self._root / name / "SKILL.md"
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
        return self._root / name.strip() / "SKILL.md"
