"""只读诊断：配置、安装元数据、DB 完整性和进程身份；不修复、不调用模型。

报告仅包含版本、计数和错误类别，不包含密钥、对话、完整工具参数或 Cookie。
"""
from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import time
import platform
import shutil
import sqlite3
from pathlib import Path

from ..config import RuntimePaths, STATE_SCHEMA_VERSION, load_config
from .process_identity import inspect_process
from .services import ServiceManager


def diagnose(paths: RuntimePaths, *, inspector=inspect_process, resolver=shutil.which) -> dict:
    checks: list[dict] = []
    def record(name: str, state: str, detail: str, **data) -> None:
        checks.append({"name": name, "state": state, "detail": detail, **data})

    try:
        config = load_config(paths.config)
        record("config", "ok" if paths.config.is_file() else "warning", "配置有效" if paths.config.is_file() else "配置文件缺失，使用默认值")
        record("models", "info", "仅检查配置；未调用模型或验证账户余额",
               routes={key: bool(value) for key, value in config.models.model_dump().items()})
        record("embedding", "info", "未加载或下载模型；未发网络请求",
               enabled=config.embedding.enabled, provider=config.embedding.provider)
        record("mcp", "info", "仅列配置数量，未启动外部服务器", configured=len(config.mcp.servers))
        napcat = Path(config.system.napcat_dir) / "napcat.bat"
        record("napcat", "ok" if napcat.is_file() else "warning", "启动文件存在" if napcat.is_file() else "未找到启动文件；未连接端口或登录")
    except Exception as exc:
        record("config", "error", f"配置无法加载：{type(exc).__name__}")
    for dependency in ("litellm", "mcp", "playwright", "sqlite-vec", "pywinpty", "pillow"):
        try:
            record(f"dependency:{dependency}", "ok", "已安装", version=importlib.metadata.version(dependency))
        except importlib.metadata.PackageNotFoundError:
            record(f"dependency:{dependency}", "warning", "依赖未安装；未自动修复")
    record("adb", "ok" if resolver("adb") else "warning", "仅解析可执行文件；未访问设备")
    for name, path in (("state.db", paths.state), ("memory.db", paths.memory)):
        if not path.is_file():
            record(name, "warning", "数据库缺失；未创建")
            continue
        connection = None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            connection.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + 5
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if not integrity or integrity[0] != "ok":
                record(name, "error", "完整性检查失败")
                continue
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("jobs", "outbox", "runs", "messages", "facts", "qq_messages") if table in tables}
            version = None
            if name == "state.db":
                row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                version = int(row[0]) if row else 0
                if not 1 <= version <= STATE_SCHEMA_VERSION:
                    record(name, "error", "schema 版本不兼容", schema=version)
                    continue
            older = version is not None and version < STATE_SCHEMA_VERSION
            record(name, "warning" if older else "ok", "旧 schema 需迁移；先备份，诊断不自动迁移" if older else "只读检查通过；未迁移",
                   schema=version, counts=counts)
        except (sqlite3.DatabaseError, OSError, ValueError) as exc:
            record(name, "error", f"数据库检查失败：{type(exc).__name__}")
        finally:
            if connection:
                connection.close()
    for name in ("napcat", "omni-qq", "omni-heartbeat"):
        path = paths.run / f"{name}.pid"
        if not path.exists():
            record(f"service:{name}", "info", "无启动记录")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            identity = ServiceManager._record_identity(data if isinstance(data, dict) else None)
            verified = (isinstance(data, dict) and data.get("version") == 1 and data.get("service") == name and
                        isinstance(data.get("launch_id"), str) and len(data["launch_id"]) == 32 and
                        identity is not None and inspector(identity.pid) == identity)
            record(f"service:{name}", "ok" if verified else "warning", "身份已核验" if verified else "身份不可核验；禁止自动终止", verified=verified)
        except (OSError, ValueError, TypeError):
            record(f"service:{name}", "warning", "启动记录损坏；未改写")
    return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "read_only": True,
            "platform": platform.system(), "python": platform.python_version(),
            "status": "error" if any(c["state"] == "error" for c in checks) else "warning" if any(c["state"] == "warning" for c in checks) else "ok",
            "checks": checks}
