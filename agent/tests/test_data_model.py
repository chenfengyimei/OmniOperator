"""数据模型文档守卫：`docs/DATA_MODEL.md` 与真实 DDL 不漂移。

state.db 的表清单从 `state_schema.py` 的迁移脚本动态提取；memory.db 的
关键表硬编码（分属多个模块，改动本来就是显式事件）。文档缺表名即失败。
"""
from __future__ import annotations

import re
from pathlib import Path

from omni.memory.state_schema import MIGRATIONS

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "DATA_MODEL.md"

# v3 迁移里的 DROP 前旧表名，已不存在于新库，文档不要求登记
HISTORICAL_TABLES = {"session_leases"}


def _state_db_tables() -> set[str]:
    tables: set[str] = set()
    for sql in MIGRATIONS.values():
        tables.update(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)", sql))
    return tables - HISTORICAL_TABLES


def test_data_model_doc_lists_every_state_db_table():
    doc = DOC.read_text(encoding="utf-8")
    missing = sorted(t for t in _state_db_tables() if f"`{t}`" not in doc)
    assert not missing, f"DATA_MODEL.md 缺少 state.db 表的登记：{missing}"


def test_data_model_doc_covers_memory_db_key_tables():
    doc = DOC.read_text(encoding="utf-8")
    key_tables = {
        "sessions", "messages", "message_contexts", "facts", "profile",
        "reviews", "bindings", "todos", "summaries", "tombstones",
        "dialogue_log", "session_log", "journal_notes", "people",
        "person_accounts", "person_entries", "fact_vectors",
    }
    missing = sorted(t for t in key_tables if f"`{t}`" not in doc)
    assert not missing, f"DATA_MODEL.md 缺少 memory.db 关键表的登记：{missing}"


def test_data_model_doc_states_migration_version_range():
    doc = DOC.read_text(encoding="utf-8")
    assert f"v1–v{max(MIGRATIONS)}" in doc, "DATA_MODEL.md 的迁移版本范围与 state_schema 不一致"


def _tables_with_session_id_column() -> set[str]:
    """从 state_schema 的 DDL 提取含 session_id 列的表（v2 已删旧表除外）。"""
    sql_all = "\n".join(MIGRATIONS.values())
    tables = {
        m.group(1)
        for m in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)\s*\((.*?)\n\);", sql_all, re.S
        )
        if "session_id" in m.group(2)
    }
    return tables - HISTORICAL_TABLES


def test_data_model_doc_lists_every_session_id_reference():
    """跨库引用清单与真实 DDL 同步：含 session_id 的表必须逐表登记在 §4。"""
    doc = DOC.read_text(encoding="utf-8")
    missing = sorted(t for t in _tables_with_session_id_column() if f"| `{t}` |" not in doc)
    assert not missing, f"DATA_MODEL.md §4 缺少 session_id 引用表的登记：{missing}"


def test_data_model_doc_records_merge_decision():
    """两库合并评估结论必须留在文档里：不合并 + 无跨库 JOIN 的依据。"""
    doc = DOC.read_text(encoding="utf-8")
    assert "不合并" in doc
    assert "没有一条" in doc and "JOIN" in doc
