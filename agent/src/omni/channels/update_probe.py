"""升级事务的数据副本迁移演练入口；不加载插件、不启动服务、不调用模型。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from ..bootstrap import build_runtime
from ..system.updates.common import UpdateError, read_json
from ..system.updates.snapshot import sqlite_copy


def rehearse(plan_path: Path) -> None:
    plan = read_json(plan_path)
    base = plan_path.parent / "rehearsal"
    base.mkdir(exist_ok=False)
    unique = {(row["state"], row["memory"], row.get("resident", "")) for row in plan["agents"]}
    for index, (state, memory, resident) in enumerate(sorted(unique)):
        target = base / str(index)
        target.mkdir()
        for source, name in ((state, "state.db"), (memory, "memory.db")):
            if Path(source).is_file():
                sqlite_copy(Path(source), target / name)
        if resident and Path(resident).is_file():
            shutil.copy2(resident, target / "MEMORY.md")
        config = target / "config.yaml"
        config.write_text("review:\n  enabled: false\nembedding:\n  enabled: false\n", encoding="utf-8")
        runtime = build_runtime(
            data_dir=target,
            db_path=target / "memory.db",
            config_path=config,
            skills_root=target / "skills",
            owner_kind="upgrade-rehearsal",
            start_background_workers=False,
            load_plugins=False,
        )
        try:
            if runtime.state.health()["degraded"]:
                raise UpdateError("migration_failed", "隔离迁移存在停用能力")
            if runtime.store._conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise UpdateError("migration_failed", "记忆数据库校验失败")
        finally:
            if not runtime.close()["complete"]:
                raise UpdateError("migration_failed", "演练实例未完整关闭")


if __name__ == "__main__":
    rehearse(Path(sys.argv[1]))
