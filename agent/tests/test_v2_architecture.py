"""V2 dependency rules and frozen-schema coverage, independent of V1 contracts."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest
from sqlalchemy import inspect

from omni.adapters.persistence.database import Database
from omni.adapters.persistence.schema import metadata
from omni.adapters.primitives import SystemClock, UUIDs

ROOT = Path(__file__).resolve().parents[1] / "src/omni"
ALLOWED = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "adapters": {"domain", "application", "adapters"},
    "composition": {"domain", "application", "adapters", "composition"},
}
IO_MODULES = {
    "os",
    "sys",
    "sqlite3",
    "sqlalchemy",
    "pathlib",
    "httpx",
    "httpx2",
    "asyncio",
    "fastapi",
    "subprocess",
    "socket",
    "litellm",
}


def issues(source: str, relative: Path) -> list[str]:
    layer = relative.parts[0]
    package = "omni." + ".".join(relative.with_suffix("").parts[:-1])
    problems = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""]
        for name in names:
            if name.startswith("omni.") and name.split(".")[1] not in ALLOWED[layer]:
                problems.append(f"Forbidden dependency: {layer} -> {name}")
            if layer == "domain" and name.split(".")[0] in IO_MODULES:
                problems.append(f"Domain I/O dependency: {name}")
            if name.split(".")[0] in {"sqlite3", "sqlalchemy", "alembic"} and relative.parts[:2] != (
                "adapters",
                "persistence",
            ):
                problems.append(f"Storage outside persistence: {name}")
    return problems


def test_v2_dependency_direction_and_no_legacy_imports():
    found = []
    for layer in ALLOWED:
        for path in (ROOT / layer).rglob("*.py"):
            found.extend(
                f"{path.relative_to(ROOT)}: {issue}"
                for issue in issues(path.read_text(encoding="utf-8"), path.relative_to(ROOT))
            )
    assert not found, "\n".join(found)


@pytest.mark.parametrize(
    "source,relative",
    [
        ("from omni.adapters.http import create_app", "domain/bad.py"),
        ("from omni.memory.store import MemoryStore", "application/bad.py"),
        ("import sqlite3", "adapters/http.py"),
        ("import pathlib", "domain/bad.py"),
    ],
)
def test_guard_rejects_bad_dependencies(source, relative):
    assert issues(source, Path(relative))


def test_frozen_migration_matches_declared_columns_and_constraints(tmp_path):
    import asyncio

    async def scenario():
        db = Database(tmp_path / "omni.db", SystemClock(), UUIDs())
        try:

            def compare(u):
                reflection = inspect(u.connection)
                for name, table in metadata.tables.items():
                    assert set(table.c.keys()) == {column["name"] for column in reflection.get_columns(name)}, name
                    declared = {tuple(constraint.column_keys) for constraint in table.foreign_key_constraints}
                    actual = {
                        tuple(constraint["constrained_columns"]) for constraint in reflection.get_foreign_keys(name)
                    }
                    assert declared == actual, name

            await db.read(compare)
        finally:
            await db.close()

    asyncio.run(scenario())
