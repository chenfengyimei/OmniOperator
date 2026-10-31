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
