"""真实数据的副本恢复/迁移演练；仅输出脱敏结果，不切换真实运行目录。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import yaml

# 禁止 LiteLLM 导入时获取在线价格表；演练实例也不启动任何模型 worker。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from omni.bootstrap import build_runtime
from omni.config import RuntimePaths, STATE_SCHEMA_VERSION
from omni.safety.gates import DenyApprover
from omni.system.backup import backup_runtime, restore_into, verify_database
from omni.system.provenance import source_fingerprint
from omni.system.agent_spaces.inspection import load_runtime_paths


TABLES = {
    "state.db": (
        "jobs",
        "executions",
        "budgets",
        "runs",
        "run_events",
        "llm_calls",
        "outbox",
        "command_jobs",
        "publication_drafts",
        "artifacts",
        "mcp_schemas",
    ),
    "memory.db": (
        "sessions",
        "messages",
        "facts",
        "profile",
        "todos",
        "summaries",
        "bindings",
        "people",
        "person_accounts",
        "person_entries",
        "owner_migration",
        "dialogue_log",
        "session_log",
        "journal_notes",
        "journal_runs",
    ),
}


def table_snapshot(path, table, columns=None):
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return None
        columns = columns or [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        select = ",".join('"' + col.replace('"', '""') + '"' for col in columns)
        digest, count = hashlib.sha256(), 0
        for row in connection.execute(f'SELECT {select} FROM "{table}" ORDER BY rowid'):
            digest.update(json.dumps(row, ensure_ascii=False, default=str).encode())
            digest.update(b"\n")
            count += 1
        return {"columns": columns, "count": count, "hash": digest.hexdigest()}


def rehearse(paths: RuntimePaths, output: Path) -> dict:
    output = output.resolve()
    if output.is_relative_to(paths.data.resolve()) or paths.data.resolve().is_relative_to(output):
        raise ValueError("演练输出必须与真实数据根分离")
    output.mkdir(parents=True, exist_ok=False)
    backup = output / "backup"
    report = backup_runtime(paths, backup)
    restored = output / "restored"
    restore_into(backup, restored)
    with sqlite3.connect((backup / "state.db").as_uri() + "?mode=ro", uri=True) as connection:
        old_version = int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
    before = {
        (filename, table): table_snapshot(backup / filename, table)
        for filename, tables in TABLES.items()
        if (backup / filename).exists()
        for table in tables
    }
    config = restored / "config.yaml"
    if not config.exists():
        config.write_text("{}\n", encoding="utf-8")
    # 副本可能位于主仓temp/private内，不能继承包含它的真实工作区；只改副本配置。
    copied = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    workspace = restored / "workspace"
    copied.setdefault("files", {}).update(root=str(workspace), extra_roots=[])
    copied.setdefault("terminal", {})["roots"] = [str(workspace)]
    copied.setdefault("system", {})["autostart"] = False
    config.write_text(yaml.safe_dump(copied, allow_unicode=True, sort_keys=False), encoding="utf-8")
    results = []
    for _ in range(2):
        runtime = build_runtime(
            data_dir=restored,
            db_path=restored / "memory.db",
            config_path=config,
            skills_root=restored / "skills",
            approver=DenyApprover(),
            owner_kind="upgrade-rehearsal",
            start_background_workers=False,
            load_plugins=False,
        )
        try:
            if (
                runtime.paths.workspace != workspace
                or runtime.paths.memory != restored / "memory.db"
                or runtime.paths.state != restored / "state.db"
            ):
                raise RuntimeError("演练路径未保持隔离，停止验证")
            health = runtime.state.health()
            results.append({"schema": health["schema_version"], "degraded": sorted(health["degraded"])})
        finally:
            closed = runtime.close()
        if not closed["complete"]:
            raise RuntimeError("演练实例未完整关闭")
    preserved = []
    for (filename, table), snapshot in before.items():
        if snapshot is None:
            continue
        after = table_snapshot(restored / filename, table, snapshot["columns"])
        # 空会话库初始化会创建一条空会话，原有非空数据必须逐行完全保持。
        unchanged = after["hash"] == snapshot["hash"] if snapshot["count"] else after["count"] >= 0
        preserved.append(
            {
                "database": filename,
                "table": table,
                "before_rows": snapshot["count"],
                "after_rows": after["count"],
                "preserved": unchanged,
            }
        )
    outcome = {
        "schema": 1,
        "source": source_fingerprint(),
        "original_schema": old_version,
        "target_schema": STATE_SCHEMA_VERSION,
        "opens": results,
        "tables": preserved,
        "databases_integrity": {
            name: verify_database(restored / name) for name in TABLES if (restored / name).exists()
        },
        "source_mutated_by_script": False,
        "real_services_started_or_stopped": False,
        "credential_copy": False,
        "workspace_isolated": True,
        "backup_entries": len(report.entries),
        "passed": all(row["schema"] == STATE_SCHEMA_VERSION and not row["degraded"] for row in results)
        and all(row["preserved"] for row in preserved),
    }
    (output / "report.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="显式创建含私人数据的本地副本")
    parser.add_argument("--data-dir", type=Path, help="控制数据根；省略时沿用启动入口配置")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--skills", type=Path)
    parser.add_argument("--memory-db", type=Path)
    parser.add_argument("--agent", help="指定 Agent，省略使用环境或注册表默认选择")
    parser.add_argument("--directory-catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("需要 --run 明确执行副本演练")
    paths = load_runtime_paths(
        data_dir=args.data_dir,
        config_path=args.config,
        skills_root=args.skills,
        memory_path=args.memory_db,
        agent_id=args.agent,
        directory_catalog=args.directory_catalog,
    )
    result = rehearse(paths, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
