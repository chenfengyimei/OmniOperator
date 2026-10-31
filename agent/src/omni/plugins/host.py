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

职责分层（2026-11-01 拆分）：
- 本模块：受限注册面（PluginContext）与受信装载生命周期；
- `plugins/package_reader.py`：包读取与类型校验（纯数据，零执行）；
- `plugins/quarantine.py`：隔离区/安装状态的文件读写与防篡改复核。

信任模型（如实声明，不假装沙箱）：Python 进程内没有可靠沙箱。校验是
数据检查，不是安全证明；**激活（人工确认）即同意执行该插件的代码**。
不可信来源走 MCP（进程外、白名单与确认链），不要做进程内插件。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..safety.gates import Risk
from ..system.atomic import atomic_write_text
from ..system.filelock import FileLock, LockTimeout
from ..tools.registry import Tool, ToolRegistry
from .manifest import MANIFEST_NAME, TOOL_NAME_RE, PluginManifest
from .package_reader import (
    MAX_ZIP_BYTES,
    Reject,
    content_sha256,
    read_directory,
    read_zip,
    validate_files,
)
from .quarantine import QuarantineStore


class PluginApiError(ValueError):
    """插件违反注册契约（命名/风险声明/参数形态）：装载失败并回滚。"""


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
    """插件宿主。一个实例持有一个注册表；多通道共享同一份。

    公开方法名自 2026-10-29 起是兼容合同；包读取/隔离状态分别在
    package_reader / quarantine 模块，本类只做生命周期编排。
    """

    def __init__(
        self,
        root: Path,
        registry: ToolRegistry,
        *,
        resolve_dir: Callable[[str], Path | None] | None = None,
        lock_timeout_s: float = 8.0,
    ) -> None:
        self._store = QuarantineStore(Path(root))
        # 兼容属性：测试与社区客户端直接访问这些目录路径；权威在 QuarantineStore
        self._root = self._store.root
        self._inbox = self._store.inbox
        self._quarantine = self._store.quarantine
        self._data = self._store.data
        self._registry = registry
        self._resolve_dir = resolve_dir
        self._lock_timeout = float(lock_timeout_s)
        self._loaded_lock = threading.Lock()
        self._loaded: dict[str, _Loaded] = {}

    @property
    def inbox_dir(self) -> Path:
        """收件箱目录（社区客户端下载落包用）。"""
        return self._inbox

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
            files = read_zip(zip_path)
            manifest = validate_files(files)
            return self._store.finalize(manifest, files, source=package)
        except Reject as exc:
            return False, f"错误：{exc}"

    def install_directory(self, rel_path: str) -> tuple[bool, str]:
        """把工作区里的插件目录（agent 自开发）校验后复制进隔离区。"""
        if self._resolve_dir is None:
            return False, "错误：本入口未接线工作区目录解析（嵌入式用法）"
        source_dir = self._resolve_dir(str(rel_path or "").strip())
        if source_dir is None or not source_dir.is_dir():
            return False, f"错误：工作区里没有目录 {rel_path or '（空）'}"
        try:
            files = read_directory(source_dir)
            manifest = validate_files(files)
            return self._store.finalize(manifest, files, source=f"工作区:{rel_path}")
        except Reject as exc:
            return False, f"错误：{exc}"

    # ---------------------------------------------------------------- 查询

    def inbox_files(self) -> list[dict[str, Any]]:
        return self._store.inbox_files()

    def pending(self) -> list[dict[str, Any]]:
        """隔离区待激活清单。"""
        return self._store.pending()

    def installed(self) -> list[dict[str, Any]]:
        """已安装清单（激活状态、启停、装载结果）。"""
        out: list[dict[str, Any]] = []
        for state in self._store.installed_states():
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
        record = self._store.package_record(plugin_id)
        if record is None:
            return False, f"错误：插件 {plugin_id} 的导入记录缺失或损坏，无法核对内容；请重新导入"
        problem = self._store.verify_quarantine(pkg_dir, record)
        if problem:
            return False, f"错误：{problem}"
        manifest = PluginManifest.load(pkg_dir / MANIFEST_NAME)
        if isinstance(manifest, str):
            return False, f"错误：{manifest}"
        if self._store.is_installed(manifest.id):
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
            if self._store.is_installed(manifest.id):
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
            "content_sha256": content_sha256(self._root / manifest.id),
            "enabled": True,
            "error": "",
        }
        atomic_write_text(
            self._store.state_path(manifest.id),
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        self._store.clear_package_record(plugin_id)
        # 激活即装载：失败不撤销激活（用户已同意代码就位），如实记录错误
        ok, tools, err = self._load(manifest)
        self._store.save_state(manifest.id, error="" if ok else err)
        if ok:
            return True, _activation_message(manifest, tools)
        return True, (
            f"已激活插件 {manifest.id}，但装载失败：{err}。"
            "代码已就位（用户已确认）；修复后用 plugin_enable 重新装载。"
        )

    def enable(self, plugin_id: str) -> tuple[bool, str]:
        """启用（首次激活后、或禁用过）的插件：指纹复核 + 重新装载。"""
        plugin_id = (plugin_id or "").strip()
        state = self._store.read_state(plugin_id)
        if state is None:
            return False, f"错误：插件 {plugin_id or '（空）'} 未安装（先 plugin_list 查看）"
        problem = self._store.verify_installed_content(plugin_id, state)
        if problem:
            return False, f"错误：{problem}"
        manifest = PluginManifest.load(self._root / plugin_id / MANIFEST_NAME)
        if isinstance(manifest, str):
            return False, f"错误：{manifest}"
        ok, tools, err = self._load(manifest)
        self._store.save_state(plugin_id, enabled=ok, error="" if ok else err)
        if not ok:
            return False, f"装载失败：{err}（工具未注册）"
        return True, f"已启用插件 {plugin_id}（工具：{'、'.join(tools) or '（未注册任何工具）'}）"

    def disable(self, plugin_id: str) -> tuple[bool, str]:
        """禁用插件：反注册它的工具；已导入的模块代码仍在进程里（如实告知）。"""
        plugin_id = (plugin_id or "").strip()
        state = self._store.read_state(plugin_id)
        if state is None:
            return False, f"错误：插件 {plugin_id or '（空）'} 未安装（先 plugin_list 查看）"
        removed = self._unload(plugin_id)
        self._store.save_state(plugin_id, enabled=False, error="")
        return True, (
            f"已禁用插件 {plugin_id}（移除工具注册：{'、'.join(removed) or '无'}）。"
            "注意：本进程已导入的模块代码不会卸载（Python 无安全卸载），"
            "重启进程后才彻底离开内存；工具入口已不可用。"
        )

    def remove(self, plugin_id: str) -> tuple[bool, str]:
        """卸载：禁用 + 删除包/状态/插件数据（调用方保证人工确认已发生）。"""
        plugin_id = (plugin_id or "").strip()
        state = self._store.read_state(plugin_id)
        if state is None:
            # 没装过：可能是隔离区里的待激活包，允许从隔离区丢弃
            if self._store.discard_quarantined(plugin_id):
                return True, f"已丢弃隔离区里的插件包 {plugin_id}"
            return False, f"错误：插件 {plugin_id or '（空）'} 未安装也不在隔离区"
        self._unload(plugin_id)
        self._store.delete_installed(plugin_id)
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
        state = self._store.read_state(plugin_id)
        if state is None:
            return False, [], "状态文件缺失"
        problem = self._store.verify_installed_content(plugin_id, state)
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


def _activation_message(manifest: PluginManifest, tools: list[str]) -> str:
    lines = [
        f"已激活插件 {manifest.id} v{manifest.version}（{manifest.name}）。",
        f"描述：{manifest.description}",
        f"已注册工具：{'、'.join(tools) or '（该插件没有注册任何工具）'}",
        "风险等级由插件作者声明并照常走审批链；群聊作用域下插件工具默认禁用。",
        "停用：plugin_disable；卸载：plugin_remove。",
    ]
    return "\n".join(lines)
