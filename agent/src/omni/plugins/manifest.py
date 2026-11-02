"""插件清单（manifest.json）：插件包的唯一身份与能力声明。

纯数据校验，零执行——校验阶段绝不 import/运行包内任何代码。

结构（v1）：
    {
      "id": "demo-tools",          # 必填：3-40 位小写字母/数字/连字符（与技能名同规则）
      "name": "演示插件",           # 必填：展示名，非空
      "version": "1.0.0",          # 必填：x.y[.z[.w]] 数字版本
      "description": "…",          # 必填：≤80 字
      "entry": "plugin.py",        # 必填：入口模块文件名（包内顶层 .py，禁路径）
      "author": "…",               # 可选
      "homepage": "…",             # 可选
      "permissions": ["tools"]     # 必填：v1 只允许 ["tools"]
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
TOOL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")
MAX_DESCRIPTION = 80
MAX_NAME = 40
MANIFEST_NAME = "manifest.json"
MAX_MANIFEST_BYTES = 16 * 1024
ALLOWED_PERMISSIONS = frozenset({"tools"})  # v1：工具注册是唯一能力口


@dataclass(frozen=True)
class PluginManifest:
    """通过校验的清单；字段都已是规范化字符串。"""

    id: str
    name: str
    version: str
    description: str
    entry: str
    author: str = ""
    homepage: str = ""
    permissions: tuple[str, ...] = ("tools",)

    @classmethod
    def from_dict(cls, data: Any) -> "PluginManifest | str":
        """dict → 清单；返回 str 表示校验失败（消息自包含）。"""
        if not isinstance(data, dict):
            return "manifest.json 必须是 JSON 对象"
        ident = str(data.get("id", "")).strip()
        if not ID_RE.match(ident):
            return "manifest 的 id 需为 3-40 位小写字母/数字/连字符（如 demo-tools）"
        name = str(data.get("name", "")).strip()
        if not name or len(name) > MAX_NAME:
            return f"manifest 的 name 必须非空且 ≤{MAX_NAME} 字"
        version = str(data.get("version", "")).strip()
        if not VERSION_RE.match(version):
            return "manifest 的 version 需为 x.y（或 x.y.z / x.y.z.w）数字版本"
        description = str(data.get("description", "")).strip()
        if not description or len(description) > MAX_DESCRIPTION:
            return f"manifest 的 description 必须非空且 ≤{MAX_DESCRIPTION} 字"
        entry = str(data.get("entry", "")).strip()
        if not entry or entry != Path(entry).name or not entry.endswith(".py") or ":" in entry:
            return "manifest 的 entry 必须是包内顶层的 .py 文件名（不带路径）"
        permissions_raw = data.get("permissions")
        if not isinstance(permissions_raw, list) or not permissions_raw:
            return 'manifest 的 permissions 必须是非空数组（v1 只支持 ["tools"]）'
        permissions = tuple(str(item).strip() for item in permissions_raw)
        unknown = [item for item in permissions if item not in ALLOWED_PERMISSIONS]
        if unknown:
            return f"manifest 声明了未知能力：{'、'.join(unknown)}（v1 只支持 tools）"
        return cls(
            id=ident,
            name=name,
            version=version,
            description=description,
            entry=entry,
            author=str(data.get("author", "")).strip(),
            homepage=str(data.get("homepage", "")).strip(),
            permissions=permissions,
        )

    @classmethod
    def load(cls, path: Path) -> "PluginManifest | str":
        """读文件 → 清单；任何读取/解析/校验失败都返回错误消息。"""
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return f"读取 manifest.json 失败（{exc}）"
        if len(raw) > MAX_MANIFEST_BYTES:
            return f"manifest.json 超过 {MAX_MANIFEST_BYTES // 1024} KiB 上限"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            return f"manifest.json 不是合法 UTF-8 JSON（{exc}）"
        return cls.from_dict(data)
