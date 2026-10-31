"""插件隔离区与安装状态的文件层：隔离区落盘、清单记录、防篡改复核。

职责边界：只做**数据文件的读写与校验**——不装载代码、不接触注册表。
`.quarantine/<id>/`（待激活包）、`<id>.package.json`（导入记录）、
`<root>/<id>.plugin.json`（安装状态）三类文件的原子写入与一致性比对都在
这里；受信装载生命周期在 `plugins/host.py`。

纪律：隔离区写入 = 暂存目录落盘 → 记录 → 原子改名，失败整目录清理；
状态写入走 `atomic_write_text`；防篡改复核按**逐文件 sha256 全等**比对，
`__pycache__` 生成物跳过（见 package_reader.is_artifact）。
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from ..system.atomic import atomic_write_text
from .manifest import MANIFEST_NAME, PluginManifest
from .package_reader import (
    MAX_CODE_BYTES,
    MAX_MANIFEST_BYTES,
    compare_manifests,
    content_sha256,
    digest,
    is_artifact,
)


class QuarantineStore:
    """隔离区 + 安装状态的文件门面。一个 root 对应一个宿主实例。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.inbox = self.root / ".inbox"
        self.quarantine = self.root / ".quarantine"
        self.data = self.root / ".data"
        for path in (self.root, self.inbox, self.quarantine, self.data):
            path.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 导入落盘

    def is_installed(self, plugin_id: str) -> bool:
        return (self.root / plugin_id).exists() or self.state_path(plugin_id).exists()

    def finalize(
        self, manifest: PluginManifest, files: dict[str, bytes], *, source: str
    ) -> tuple[bool, str]:
        """写入隔离区：暂存目录落盘 → 清单记录 → 原子改名。"""
        if self.is_installed(manifest.id):
            return False, (
                f"错误：插件 {manifest.id} 已安装。先 plugin_remove 卸载，"
                "或先查看现有插件（plugin_list）"
            )
        staging = self.quarantine / f".staging-{uuid.uuid4().hex[:12]}"
        staging.mkdir(parents=True)
        try:
            for rel, data in files.items():
                target = staging.joinpath(*rel.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            target_dir = self.quarantine / manifest.id
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            staging.rename(target_dir)
            record = {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "source": source,
                "imported_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "files": [
                    {"path": rel, "bytes": len(data), "sha256": digest(data)}
                    for rel, data in sorted(files.items())
                ],
            }
            atomic_write_text(
                self.quarantine / f"{manifest.id}.package.json",
                json.dumps(record, ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return False, f"错误：写入隔离区失败（{exc}），未安装"
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        lines = [
            f"已导入 {source} 到隔离区（插件 {manifest.id} v{manifest.version}，"
            f"{len(files)} 个文件）：",
            f"- {manifest.name}：{manifest.description}",
            f"- 入口：{manifest.entry}；来源：{source}",
        ]
        for rel, data in sorted(files.items()):
            lines.append(f"- {rel}（{len(data)} B）")
        lines.append(
            "隔离区里的包不会执行任何代码。审阅后由用户确认：plugin_activate 激活"
            "（激活即同意执行其代码），或 plugin_remove 丢弃。"
        )
        return True, "\n".join(lines)

    # ---------------------------------------------------------------- 查询

    def inbox_files(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            for path in sorted(self.inbox.iterdir()):
                if path.is_file() and path.suffix.lower() == ".zip":
                    try:
                        out.append({"name": path.name, "bytes": path.stat().st_size})
                    except OSError:
                        continue
        except OSError:
            pass
        return out

    def pending(self) -> list[dict[str, Any]]:
        """隔离区待激活清单。"""
        out: list[dict[str, Any]] = []
        for pkg_dir in sorted(self.quarantine.glob("*")):
            if not pkg_dir.is_dir() or not (pkg_dir / MANIFEST_NAME).is_file():
                continue
            record = self.package_record(pkg_dir.name)
            if record is None:
                out.append(
                    {
                        "id": pkg_dir.name,
                        "name": "",
                        "version": "",
                        "description": "",
                        "source": "",
                        "imported_at": "",
                        "files": [],
                        "record_missing": True,
                    }
                )
                continue
            out.append(
                {
                    "id": record.get("id", pkg_dir.name),
                    "name": str(record.get("name", "")),
                    "version": str(record.get("version", "")),
                    "description": str(record.get("description", "")),
                    "source": str(record.get("source", "")),
                    "imported_at": str(record.get("imported_at", "")),
                    "files": [
                        {"path": str(item.get("path", "")), "bytes": int(item.get("bytes", 0))}
                        for item in record.get("files", [])
                        if isinstance(item, dict)
                    ],
                    "record_missing": False,
                }
            )
        return out

    def discard_quarantined(self, plugin_id: str) -> bool:
        """丢弃隔离区里的待激活包（不含已安装插件）；返回是否真的删了。"""
        pkg_dir = self.quarantine / plugin_id
        if not (plugin_id and pkg_dir.is_dir()):
            return False
        shutil.rmtree(pkg_dir, ignore_errors=True)
        (self.quarantine / f"{plugin_id}.package.json").unlink(missing_ok=True)
        return True

    # ---------------------------------------------------------------- 安装状态

    def state_path(self, plugin_id: str) -> Path:
        return self.root / f"{plugin_id}.plugin.json"

    def read_state(self, plugin_id: str) -> dict[str, Any] | None:
        try:
            state = json.loads(self.state_path(plugin_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def save_state(self, plugin_id: str, **updates: Any) -> None:
        state = self.read_state(plugin_id) or {"id": plugin_id}
        state.update(updates)
        atomic_write_text(
            self.state_path(plugin_id), json.dumps(state, ensure_ascii=False, indent=2)
        )

    def installed_states(self) -> list[dict[str, Any]]:
        """全部已安装状态（按文件名序）。损坏的状态文件静默跳过（查询不炸）。"""
        out: list[dict[str, Any]] = []
        for state_path in sorted(self.root.glob("*.plugin.json")):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(state, dict):
                out.append(state)
        return out

    def delete_installed(self, plugin_id: str) -> None:
        """卸载清理：包目录、插件数据目录与状态文件一起删除。"""
        shutil.rmtree(self.root / plugin_id, ignore_errors=True)
        shutil.rmtree(self.data / plugin_id, ignore_errors=True)
        self.state_path(plugin_id).unlink(missing_ok=True)

    # ---------------------------------------------------------------- 记录与防篡改

    def package_record(self, plugin_id: str) -> dict[str, Any] | None:
        try:
            record = json.loads(
                (self.quarantine / f"{plugin_id}.package.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict) or not isinstance(record.get("files"), list):
            return None
        return record

    def clear_package_record(self, plugin_id: str) -> None:
        (self.quarantine / f"{plugin_id}.package.json").unlink(missing_ok=True)

    def verify_quarantine(self, pkg_dir: Path, record: dict[str, Any]) -> str | None:
        """激活前防篡改：隔离区当前内容与导入记录逐文件 sha256 全等比对。"""
        recorded: dict[str, str] = {}
        for item in record.get("files", []):
            if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
                return "导入记录不完整，无法核对；请重新导入"
            recorded[str(item["path"])] = str(item["sha256"])
        actual: dict[str, str] = {}
        try:
            for path in sorted(pkg_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel_parts = tuple(path.relative_to(pkg_dir).as_posix().split("/"))
                if is_artifact(rel_parts):
                    continue
                rel = "/".join(rel_parts)
                data = path.read_bytes()
                if len(data) > MAX_CODE_BYTES + MAX_MANIFEST_BYTES:
                    return f"文件 {rel} 超过大小上限，疑似被替换；请重新导入"
                actual[rel] = digest(data)
        except OSError as exc:
            return f"读取隔离区内容失败（{exc}）；请重新导入"
        return compare_manifests(recorded, actual)

    def verify_installed_content(self, plugin_id: str, state: dict[str, Any]) -> str | None:
        """启用前防篡改：包内容指纹必须与激活时一致。"""
        expected = str(state.get("content_sha256", ""))
        if not expected:
            return None
        current = content_sha256(self.root / plugin_id)
        if current != expected:
            return (
                "包内容与激活时不一致（激活后被改动过）。"
                "确认改动后请重新走 plugin_install → plugin_activate"
            )
        return None
