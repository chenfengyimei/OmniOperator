"""插件宿主：外来功能包的隔离安装、激活、装载与卸载。

生命周期（与外来技能包同一条纪律线）：
- 收包：zip 放 `data/plugins/.inbox/`，或从工作区目录（agent 自开发闭环）
  `plugin_install(path=...)`；
- 隔离：数据化校验解包进 `.quarantine/<id>/`——**绝不执行任何代码**：
  类型白名单（manifest.json + 唯一顶层 entry.py + README.md + references 文本）、
  zip-slip/盘符/ADS/保留名全防、体积与数量上限；
- 激活：`plugin_activate`（CONFIRM + 人工确认）把包移进 `data/plugins/<id>/`，
  激活前对隔离区做**防篡改复核**（与导入清单逐文件 sha256 全等比对）；
- 装载：导入 entry 模块并调用其 `register(ctx)`——ctx 是受限注册面：
  工具名强制 `<id>-` 前缀（不能影子覆盖内置工具）、toolset=plugin:<id>
  （可归因、群聊作用域自动禁用）、风险等级由作者声明并照常走既有审批链；
- 启停/卸载：disable 反注册工具（**已导入的模块代码无法从进程里卸掉**，
  如实告知不假装）；remove 删除包与状态（同样只声称"工具已不可用"）。

信任模型（如实声明，不假装沙箱）：Python 进程内没有可靠沙箱。校验是
数据检查，不是安全证明；**激活（人工确认）即同意执行该插件的代码**。
不可信来源走 MCP（进程外、白名单与确认链），不要做进程内插件。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import shutil
import sys
import threading
import uuid
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from ..safety.gates import Risk
from ..system.atomic import atomic_write_text
from ..system.filelock import FileLock, LockTimeout
from ..tools.registry import Tool, ToolRegistry
from .manifest import (
    MANIFEST_NAME,
    PluginManifest,
    TOOL_NAME_RE,
    MAX_MANIFEST_BYTES,
)

MAX_ZIP_BYTES = 4 * 1024 * 1024        # 压缩包本身 ≤ 4 MiB
MAX_UNPACKED_BYTES = 8 * 1024 * 1024    # 解包总量 ≤ 8 MiB
MAX_ENTRIES = 50                        # 条目数上限（含目录记录）
MAX_CODE_BYTES = 128 * 1024             # entry 单文件 ≤ 128 KiB
MAX_REFERENCE_BYTES = 64 * 1024         # 单个参考文件 ≤ 64 KiB
MAX_REFERENCES = 20
MAX_REFERENCE_DEPTH = 4
REFERENCE_SUFFIXES = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv"}


class _Reject(RuntimeError):
    """导入校验失败：整包拒绝，隔离区不落任何文件。"""


class PluginApiError(ValueError):
    """插件违反注册契约（命名/风险声明/参数形态）：装载失败并回滚。"""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_parts(entry: str) -> tuple[str, ...] | None:
    """包内条目名 → 规范化相对部件；越界名拒绝（与 memory/skill_inbox 同一路径纪律）。"""
    name = (entry or "").strip().replace("\\", "/")
    if not name or name.startswith("/"):
        return None
    if PureWindowsPath(entry).drive or PureWindowsPath(entry).is_absolute():
        return None
    parts = name.split("/")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or ":" in part
            or "\x00" in part
            or PureWindowsPath(part).is_reserved()
        ):
            return None
    return tuple(parts)


def _entry_shape(parts: tuple[str, ...]) -> str | None:
    """落位后的条目形状；v1 白名单：manifest / 唯一顶层 py / README / references 文本。"""
    if parts == ("manifest.json",):
        return "manifest"
    if len(parts) == 1 and parts[0].endswith(".py"):
        return "code"
    if parts == ("README.md",):
        return "readme"
    if len(parts) < 2 or parts[0] != "references" or len(parts) > MAX_REFERENCE_DEPTH:
        return None
    if Path(parts[-1]).suffix.lower() not in REFERENCE_SUFFIXES:
        return None
    return "ref"


def _is_artifact(parts: tuple[str, ...]) -> bool:
    """Python 生成的字节码缓存：不属于包内容——读包、指纹与防篡改一律跳过。

    背景：导入插件时解释器会在包目录写 __pycache__/*.pyc；工作区开发目录里
    也常有本地调试留下的缓存。它们不是作者内容，也不被加载（入口按源码加载）。
    """
    return bool(parts) and parts[0] == "__pycache__"


class PluginContext:
    """给插件代码的受限注册面：只暴露 add_tool 与数据目录，不给运行时内部件。

    刻意的窄接口：插件拿不到 ToolRegistry 本体（不能改别人/不能反注册），
    工具名必须带 `<id>-` 前缀——归因可见，且结构上不可能影子覆盖内置工具。
    """

    def __init__(self, plugin_id: str, registry: ToolRegistry, data_dir: Path) -> None:
        self.id = plugin_id
        self.data_dir = data_dir  # 插件私有数据目录（随卸载一起删除）
        self._registry = registry
        self._registered: list[str] = []

    @property
    def registered_tools(self) -> list[str]:
        return list(self._registered)

    def add_tool(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[[dict[str, Any]], Any],
        risk: str = "safe",
        requires_human: bool = False,
    ) -> None:
        """注册一个工具；违反契约抛 PluginApiError（装载失败、整组回滚）。"""
        name = str(name or "").strip()
        prefix = f"{self.id}-"
        if not TOOL_NAME_RE.match(name) or not name.startswith(prefix):
            raise PluginApiError(
                f"工具名必须以 {prefix!r} 开头（如 {prefix}summarize），"
                "避免覆盖内置工具与归因混淆"
            )
        description = str(description or "").strip()
        if not description or len(description) > 500:
            raise PluginApiError(f"工具 {name} 的 description 必须非空且 ≤500 字")
        if not isinstance(parameters, dict) or not parameters:
            raise PluginApiError(f"工具 {name} 的 parameters 必须是非空 JSON Schema 对象")
        if not callable(func):
            raise PluginApiError(f"工具 {name} 的 func 必须可调用")
        if str(risk) not in {"safe", "confirm", "dangerous"}:
            raise PluginApiError(
                f"工具 {name} 的 risk 必须是 safe/confirm/dangerous（作者声明，照常走审批链）"
            )
        if self._registry.get(name) is not None:
            raise PluginApiError(f"工具 {name} 已被占用（内置或其它插件）；换一个名字")
        self._registry.register(
            Tool(
                name=name,
                description=description,
                parameters=parameters,
                func=func,
                toolset=f"plugin:{self.id}",
                risk=Risk(str(risk)),
                requires_human=bool(requires_human),
            )
        )
        self._registered.append(name)


class _Loaded:
    """一个已装载插件在进程里的足迹。"""

    def __init__(self, manifest: PluginManifest, tool_names: list[str], module_name: str):
        self.manifest = manifest
        self.tool_names = tool_names
        self.module_name = module_name


class PluginHost:
    """插件宿主。一个实例持有一个注册表；多通道共享同一份。"""

    def __init__(
        self,
        root: Path,
        registry: ToolRegistry,
        *,
        resolve_dir: Callable[[str], Path | None] | None = None,
        lock_timeout_s: float = 8.0,
    ) -> None:
        self._root = Path(root)
        self._inbox = self._root / ".inbox"
        self._quarantine = self._root / ".quarantine"
        self._data = self._root / ".data"
        self._registry = registry
        self._resolve_dir = resolve_dir
        self._lock_timeout = float(lock_timeout_s)
        self._loaded_lock = threading.Lock()
        self._loaded: dict[str, _Loaded] = {}
        for path in (self._root, self._inbox, self._quarantine, self._data):
            path.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 导入（数据化，零执行）

    def install_package(self, package: str) -> tuple[bool, str]:
        """把 inbox 里的一个插件 zip 校验后放进隔离区。"""
        package = (package or "").strip()
        if not package or package != Path(package).name:
            return False, "错误：package 必须是 data/plugins/.inbox/ 里的 zip 文件名，不带路径"
        zip_path = self._inbox / package
        if not zip_path.is_file():
            return False, f"错误：inbox 里没有 {package or '（空）'}。请先把插件包 zip 放进去"
        try:
            size = zip_path.stat().st_size
        except OSError as exc:
            return False, f"错误：读取压缩包失败（{exc}）"
        if size > MAX_ZIP_BYTES:
            return False, f"错误：压缩包超过 {MAX_ZIP_BYTES // (1024 * 1024)} MiB 上限"
        try:
            files = self._read_zip(zip_path)
            manifest = self._validate_files(files)
            return self._finalize_quarantine(manifest, files, source=package)
        except _Reject as exc:
            return False, f"错误：{exc}"

    def install_directory(self, rel_path: str) -> tuple[bool, str]:
        """把工作区里的插件目录（agent 自开发）校验后复制进隔离区。"""
        if self._resolve_dir is None:
            return False, "错误：本入口未接线工作区目录解析（嵌入式用法）"
        source_dir = self._resolve_dir(str(rel_path or "").strip())
        if source_dir is None or not source_dir.is_dir():
            return False, f"错误：工作区里没有目录 {rel_path or '（空）'}"
        try:
            files = self._read_directory(source_dir)
            manifest = self._validate_files(files)
            return self._finalize_quarantine(manifest, files, source=f"工作区:{rel_path}")
        except _Reject as exc:
            return False, f"错误：{exc}"

    # ---------------------------------------------------------------- 校验内部

    def _read_zip(self, zip_path: Path) -> dict[str, bytes]:
        """读 zip → {规范化相对路径: 字节}；任何越界/白名单外条目整包拒绝。"""
        try:
            zf = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise _Reject(f"不是可读的 zip 压缩包（{exc}）") from exc
        with zf:
            infos = zf.infolist()
            if not infos:
                raise _Reject("压缩包是空的")
            if len(infos) > MAX_ENTRIES:
                raise _Reject(f"压缩包条目超过 {MAX_ENTRIES} 个上限（当前 {len(infos)}）")
            raw: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
            for info in infos:
                parts = _safe_parts(info.filename)
                if parts is None:
                    raise _Reject(f"条目名不安全，整包拒绝：{info.filename!r}")
                if info.is_dir() or _is_artifact(parts):
                    continue  # 目录记录与 __pycache__ 缓存不落盘
                raw.append((info, parts))
            if not raw:
                raise _Reject("压缩包里没有文件")
            # 剥掉公共顶层目录（"打包整个文件夹"的常见 zip 形态），剥前已过名字安全检查
            firsts = {parts[0] for _, parts in raw}
            if len(firsts) == 1 and any(len(parts) > 1 for _, parts in raw):
                raw = [(info, parts[1:]) for info, parts in raw]
            files: dict[str, bytes] = {}
            unpacked = 0
            seen: set[tuple[str, ...]] = set()
            for info, parts in raw:
                shape = _entry_shape(parts)
                if shape is None:
                    raise _Reject(
                        f"条目 {info.filename} 不在类型白名单内（只允许 manifest.json、"
                        "唯一顶层入口 .py、README.md 与 references/ 文本）"
                    )
                if parts in seen:
                    raise _Reject(f"条目重名（{info.filename!r}），整包拒绝")
                seen.add(parts)
                unpacked += info.file_size
                cap = MAX_MANIFEST_BYTES if shape == "manifest" else (
                    MAX_CODE_BYTES if shape == "code" else MAX_REFERENCE_BYTES
                )
                try:
                    with zf.open(info) as stream:
                        data = stream.read(cap + 1)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise _Reject(f"读取条目 {info.filename} 失败（{exc}）") from exc
                if len(data) > cap:
                    raise _Reject(f"条目 {info.filename} 超过大小上限")
                files["/".join(parts)] = data
            if unpacked > MAX_UNPACKED_BYTES:
                raise _Reject(f"解包总量超过 {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB 上限")
        return files

    def _read_directory(self, source_dir: Path) -> dict[str, bytes]:
        """读工作区目录 → {相对路径: 字节}；与 zip 同一套形状/上限校验。"""
        files: dict[str, bytes] = {}
        total = 0
        count = 0
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = tuple(path.relative_to(source_dir).as_posix().split("/"))
            if _is_artifact(rel_parts):
                continue  # 开发时留下的字节码缓存不属于包内容
            count += 1
            if count > MAX_ENTRIES:
                raise _Reject(f"目录条目超过 {MAX_ENTRIES} 个上限")
            parts = tuple(path.relative_to(source_dir).as_posix().split("/"))
            shape = _entry_shape(parts)
            if shape is None:
                raise _Reject(
                    f"文件 {path.name} 不在类型白名单内（只允许 manifest.json、"
                    "唯一顶层入口 .py、README.md 与 references/ 文本）"
                )
            cap = MAX_MANIFEST_BYTES if shape == "manifest" else (
                MAX_CODE_BYTES if shape == "code" else MAX_REFERENCE_BYTES
            )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise _Reject(f"读取 {path.name} 失败（{exc}）") from exc
            if len(data) > cap:
                raise _Reject(f"文件 {path.name} 超过大小上限")
            total += len(data)
            files["/".join(parts)] = data
        if not files:
            raise _Reject("目录是空的")
        if total > MAX_UNPACKED_BYTES:
            raise _Reject(f"内容总量超过 {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB 上限")
        return files

    def _validate_files(self, files: dict[str, bytes]) -> PluginManifest:
        """清单解析 + 形状终检（入口必须恰好是唯一顶层 .py）。"""
        raw = files.get(MANIFEST_NAME)
        if raw is None:
            raise _Reject("包里没有 manifest.json（应在顶层或唯一顶层目录之内）")
        try:
            manifest = PluginManifest.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise _Reject(f"manifest.json 不是合法 UTF-8 JSON（{exc}）") from exc
        if isinstance(manifest, str):
            raise _Reject(manifest)
        py_files = [name for name in files if name.endswith(".py") and "/" not in name]
        if py_files != [manifest.entry]:
            raise _Reject(
                f"入口必须恰好是唯一的顶层 .py：manifest 声明 {manifest.entry}，"
                f"实际有 {py_files or '（无）'}"
            )
        refs = [name for name in files if name.startswith("references/")]
        if len(refs) > MAX_REFERENCES:
            raise _Reject(f"references 文件超过 {MAX_REFERENCES} 个上限（当前 {len(refs)}）")
        return manifest

    def _finalize_quarantine(
        self, manifest: PluginManifest, files: dict[str, bytes], *, source: str
    ) -> tuple[bool, str]:
        """写入隔离区：暂存目录落盘 → 清单记录 → 原子改名。"""
        if (self._root / manifest.id).exists() or self._state_path(manifest.id).exists():
            return False, (
                f"错误：插件 {manifest.id} 已安装。先 plugin_remove 卸载，"
                "或先查看现有插件（plugin_list）"
            )
        staging = self._quarantine / f".staging-{uuid.uuid4().hex[:12]}"
        staging.mkdir(parents=True)
        try:
            for rel, data in files.items():
                target = staging.joinpath(*rel.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            target_dir = self._quarantine / manifest.id
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
                    {"path": rel, "bytes": len(data), "sha256": _digest(data)}
                    for rel, data in sorted(files.items())
                ],
            }
            atomic_write_text(
                self._quarantine / f"{manifest.id}.package.json",
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
            for path in sorted(self._inbox.iterdir()):
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
        for pkg_dir in sorted(self._quarantine.glob("*")):
            if not pkg_dir.is_dir() or not (pkg_dir / MANIFEST_NAME).is_file():
                continue
            record = self._package_record(pkg_dir.name)
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

    def installed(self) -> list[dict[str, Any]]:
        """已安装清单（激活状态、启停、装载结果）。"""
        out: list[dict[str, Any]] = []
        for state_path in sorted(self._root.glob("*.plugin.json")):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict):
                continue
            plugin_id = str(state.get("id", ""))
            manifest = PluginManifest.load(self._root / plugin_id / MANIFEST_NAME)
            meta = None if isinstance(manifest, str) else manifest
            with self._loaded_lock:
                loaded = self._loaded.get(plugin_id)
            out.append(
                {
                    "id": plugin_id,
                    "name": meta.name if meta else str(state.get("name", "")),
                    "version": str(state.get("version", "")),
                    "description": meta.description if meta else "",
                    "source": str(state.get("source", "")),
                    "enabled": bool(state.get("enabled", False)),
                    "activated_at": str(state.get("activated_at", "")),
                    "tools": list(loaded.tool_names) if loaded else [],
                    "error": str(state.get("error", "")),
                }
            )
        return out

    # ---------------------------------------------------------------- 激活 / 启停 / 卸载

    def activate(self, plugin_id: str) -> tuple[bool, str]:
        """把隔离区插件激活进正式目录（调用方保证人工确认已发生）。"""
        plugin_id = (plugin_id or "").strip()
        pkg_dir = self._quarantine / plugin_id
        if not (pkg_dir / MANIFEST_NAME).is_file():
            return False, f"错误：隔离区没有待激活的插件 {plugin_id or '（空）'}（先 plugin_install）"
        record = self._package_record(plugin_id)
        if record is None:
            return False, f"错误：插件 {plugin_id} 的导入记录缺失或损坏，无法核对内容；请重新导入"
        problem = self._verify_quarantine(pkg_dir, record)
        if problem:
            return False, f"错误：{problem}"
        manifest = PluginManifest.load(pkg_dir / MANIFEST_NAME)
        if isinstance(manifest, str):
            return False, f"错误：{manifest}"
        if (self._root / manifest.id).exists() or self._state_path(manifest.id).exists():
            return False, f"错误：插件 {manifest.id} 已安装，拒绝覆盖"
        lock = FileLock(
            self._root / ".locks" / f"{manifest.id}.lock",
            timeout_s=self._lock_timeout,
            owner=f"plugin-host:{manifest.id}",
        )
        try:
            lock.acquire()
        except LockTimeout as exc:
            return False, f"错误：插件 {manifest.id} 正被其它进程操作（{exc}），稍后再试"
        try:
            if (self._root / manifest.id).exists() or self._state_path(manifest.id).exists():
                return False, f"错误：插件 {manifest.id} 已安装，拒绝覆盖"
            pkg_dir.rename(self._root / manifest.id)
        except OSError as exc:
            return False, f"错误：激活失败（{exc}），隔离区保持原样"
        finally:
            lock.release()
        state = {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "source": str(record.get("source", "")),
            "activated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "content_sha256": _content_sha256(self._root / manifest.id),
            "enabled": True,
            "error": "",
        }
        atomic_write_text(self._state_path(manifest.id), json.dumps(state, ensure_ascii=False, indent=2))
        (self._quarantine / f"{plugin_id}.package.json").unlink(missing_ok=True)
        # 激活即装载：失败不撤销激活（用户已同意代码就位），如实记录错误
        ok, tools, err = self._load(manifest)
        self._save_state(manifest.id, error="" if ok else err)
        if ok:
            return True, _activation_message(manifest, tools)
        return True, (
            f"已激活插件 {manifest.id}，但装载失败：{err}。"
            "代码已就位（用户已确认）；修复后用 plugin_enable 重新装载。"
        )

    def enable(self, plugin_id: str) -> tuple[bool, str]:
        """启用（首次激活后、或禁用过）的插件：指纹复核 + 重新装载。"""
        plugin_id = (plugin_id or "").strip()
        state = self._read_state(plugin_id)
        if state is None:
            return False, f"错误：插件 {plugin_id or '（空）'} 未安装（先 plugin_list 查看）"
        problem = self._verify_installed_content(plugin_id, state)
        if problem:
            return False, f"错误：{problem}"
        manifest = PluginManifest.load(self._root / plugin_id / MANIFEST_NAME)
        if isinstance(manifest, str):
            return False, f"错误：{manifest}"
        ok, tools, err = self._load(manifest)
        self._save_state(plugin_id, enabled=ok, error="" if ok else err)
        if not ok:
            return False, f"装载失败：{err}（工具未注册）"
        return True, f"已启用插件 {plugin_id}（工具：{'、'.join(tools) or '（未注册任何工具）'}）"

    def disable(self, plugin_id: str) -> tuple[bool, str]:
        """禁用插件：反注册它的工具；已导入的模块代码仍在进程里（如实告知）。"""
        plugin_id = (plugin_id or "").strip()
        state = self._read_state(plugin_id)
        if state is None:
            return False, f"错误：插件 {plugin_id or '（空）'} 未安装（先 plugin_list 查看）"
        removed = self._unload(plugin_id)
        self._save_state(plugin_id, enabled=False, error="")
        return True, (
            f"已禁用插件 {plugin_id}（移除工具注册：{'、'.join(removed) or '无'}）。"
            "注意：本进程已导入的模块代码不会卸载（Python 无安全卸载），"
            "重启进程后才彻底离开内存；工具入口已不可用。"
        )

    def remove(self, plugin_id: str) -> tuple[bool, str]:
        """卸载：禁用 + 删除包/状态/插件数据（调用方保证人工确认已发生）。"""
        plugin_id = (plugin_id or "").strip()
        state = self._read_state(plugin_id)
        if state is None:
            # 没装过：可能是隔离区里的待激活包，允许从隔离区丢弃
            pkg_dir = self._quarantine / plugin_id
            if plugin_id and pkg_dir.is_dir():
                shutil.rmtree(pkg_dir, ignore_errors=True)
                (self._quarantine / f"{plugin_id}.package.json").unlink(missing_ok=True)
                return True, f"已丢弃隔离区里的插件包 {plugin_id}"
            return False, f"错误：插件 {plugin_id or '（空）'} 未安装也不在隔离区"
        self._unload(plugin_id)
        shutil.rmtree(self._root / plugin_id, ignore_errors=True)
        shutil.rmtree(self._data / plugin_id, ignore_errors=True)
        self._state_path(plugin_id).unlink(missing_ok=True)
        return True, (
            f"已卸载插件 {plugin_id}（包、状态与插件数据已删除；"
            "本进程已导入的模块代码不会卸载，重启后彻底清除）。"
        )

    def load_installed(self) -> list[str]:
        """启动时恢复所有已启用的插件（bootstrap 调用）；返回装载失败清单。"""
        failures: list[str] = []
        for item in self.installed():
            if not item["enabled"]:
                continue
            ok, _tools, err = self._load_from_state(item["id"])
            if not ok:
                failures.append(f"{item['id']}: {err}")
        return failures

    # ---------------------------------------------------------------- 装载内部

    def _load_from_state(self, plugin_id: str) -> tuple[bool, list[str], str]:
        state = self._read_state(plugin_id)
        if state is None:
            return False, [], "状态文件缺失"
        problem = self._verify_installed_content(plugin_id, state)
        if problem:
            return False, [], problem
        manifest = PluginManifest.load(self._root / plugin_id / MANIFEST_NAME)
        if isinstance(manifest, str):
            return False, [], manifest
        return self._load(manifest)

    def _load(self, manifest: PluginManifest) -> tuple[bool, list[str], str]:
        """导入 entry 模块并执行 register(ctx)。激活即同意执行；失败整组回滚。"""
        self._unload(manifest.id)  # 幂等：重装载前先清掉旧注册
        entry_path = self._root / manifest.id / manifest.entry
        module_name = f"omni_plugin_{manifest.id}_{uuid.uuid4().hex[:8]}"
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            return False, [], f"入口 {manifest.entry} 无法作为 Python 模块加载"
        module = importlib.util.module_from_spec(spec)
        data_dir = self._data / manifest.id
        data_dir.mkdir(parents=True, exist_ok=True)
        context = PluginContext(manifest.id, self._registry, data_dir)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # 受信边界：激活即同意执行
            register = getattr(module, "register", None)
            if not callable(register):
                raise PluginApiError(f"入口 {manifest.entry} 未定义 register(ctx) 函数")
            register(context)
        except Exception as exc:  # noqa: BLE001 —— 插件装载失败必须被关在插件内
            self._remove_registrations(context.registered_tools, manifest.id)
            sys.modules.pop(module_name, None)
            return False, [], f"{type(exc).__name__}: {exc}"
        with self._loaded_lock:
            self._loaded[manifest.id] = _Loaded(
                manifest, context.registered_tools, module_name
            )
        return True, context.registered_tools, ""

    def _unload(self, plugin_id: str) -> list[str]:
        """反注册工具并忘掉装载足迹；返回移除的工具名。"""
        with self._loaded_lock:
            loaded = self._loaded.pop(plugin_id, None)
        if loaded is None:
            return []
        self._remove_registrations(loaded.tool_names, plugin_id)
        sys.modules.pop(loaded.module_name, None)
        return list(loaded.tool_names)

    def _remove_registrations(self, names: list[str], plugin_id: str) -> None:
        """只反注册属于本插件（toolset 对得上）的工具，不碰别人的。"""
        for name in names:
            tool = self._registry.get(name)
            if tool is not None and tool.toolset == f"plugin:{plugin_id}":
                self._registry.unregister(name)

    # ---------------------------------------------------------------- 状态与校验

    def _state_path(self, plugin_id: str) -> Path:
        return self._root / f"{plugin_id}.plugin.json"

    def _read_state(self, plugin_id: str) -> dict[str, Any] | None:
        try:
            state = json.loads(self._state_path(plugin_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def _save_state(self, plugin_id: str, **updates: Any) -> None:
        state = self._read_state(plugin_id) or {"id": plugin_id}
        state.update(updates)
        atomic_write_text(
            self._state_path(plugin_id), json.dumps(state, ensure_ascii=False, indent=2)
        )

    def _package_record(self, plugin_id: str) -> dict[str, Any] | None:
        try:
            record = json.loads(
                (self._quarantine / f"{plugin_id}.package.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict) or not isinstance(record.get("files"), list):
            return None
        return record

    def _verify_quarantine(self, pkg_dir: Path, record: dict[str, Any]) -> str | None:
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
                if _is_artifact(rel_parts):
                    continue
                rel = "/".join(rel_parts)
                data = path.read_bytes()
                if len(data) > MAX_CODE_BYTES + MAX_MANIFEST_BYTES:
                    return f"文件 {rel} 超过大小上限，疑似被替换；请重新导入"
                actual[rel] = _digest(data)
        except OSError as exc:
            return f"读取隔离区内容失败（{exc}）；请重新导入"
        return _compare_manifests(recorded, actual)

    def _verify_installed_content(self, plugin_id: str, state: dict[str, Any]) -> str | None:
        """启用前防篡改：包内容指纹必须与激活时一致。"""
        expected = str(state.get("content_sha256", ""))
        if not expected:
            return None
        current = _content_sha256(self._root / plugin_id)
        if current != expected:
            return (
                "包内容与激活时不一致（激活后被改动过）。"
                "确认改动后请重新走 plugin_install → plugin_activate"
            )
        return None


def _content_sha256(pkg_dir: Path) -> str:
    """包内容指纹：全部**非生成物**文件按路径序做 sha256（升级/篡改都以它为准）。"""
    digest = hashlib.sha256()
    try:
        for path in sorted(pkg_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(pkg_dir).as_posix()
            if rel.split("/")[0] == "__pycache__":
                continue  # 解释器生成的字节码不属于内容
            digest.update(rel.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(path.read_bytes())
            digest.update(b"\x00")
    except OSError:
        pass
    return digest.hexdigest()


def _compare_manifests(recorded: dict[str, str], actual: dict[str, str]) -> str | None:
    if recorded == actual:
        return None
    changed = sorted(k for k in actual if k in recorded and actual[k] != recorded[k])
    extra = sorted(k for k in actual if k not in recorded)
    missing = sorted(k for k in recorded if k not in actual)
    detail = "；".join(
        part
        for part in (
            f"被改动：{'、'.join(changed)}" if changed else "",
            f"新增：{'、'.join(extra)}" if extra else "",
            f"缺失：{'、'.join(missing)}" if missing else "",
        )
        if part
    )
    return f"隔离区内容与导入记录不一致（{detail}）；可能被改动过，请重新导入"


def _activation_message(manifest: PluginManifest, tools: list[str]) -> str:
    lines = [
        f"已激活插件 {manifest.id} v{manifest.version}（{manifest.name}）。",
        f"描述：{manifest.description}",
        f"已注册工具：{'、'.join(tools) or '（该插件没有注册任何工具）'}",
        "风险等级由插件作者声明并照常走审批链；群聊作用域下插件工具默认禁用。",
        "停用：plugin_disable；卸载：plugin_remove。",
    ]
    return "\n".join(lines)
