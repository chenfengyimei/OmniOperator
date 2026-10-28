"""架构守卫：用 AST 强制分层依赖规则与组装根规则（docs/ARCHITECTURE.md §3）。

规则一（分层）：下层包不允许 import 上层/同级包，防止依赖倒挂；
规则二（组装根）：Gateway / MemoryStore / ToolRegistry / Agent 只允许在
bootstrap.py 中构造，通道与内核只做注入消费。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "omni"

# 各层禁止 import 的 omni 内部顶层模块（channels / bootstrap 是顶层，不设限）。
# 依赖方向（自上而下）：channels/bootstrap → core → llm/memory/tools/safety → config；
# config 与 safety 是叶子层，不允许 import 任何内部模块。
FORBIDDEN_IMPORTS: dict[str, set[str]] = {
    "config": {"memory", "llm", "tools", "safety", "core", "channels", "bootstrap"},
    "safety": {"config", "memory", "llm", "tools", "core", "channels", "bootstrap"},
    "memory": {"llm", "tools", "safety", "core", "channels", "bootstrap"},
    "llm": {"memory", "tools", "safety", "core", "channels", "bootstrap"},
    "tools": {"core", "llm", "channels", "bootstrap"},
    "core": {"channels", "bootstrap"},
}

# 只允许在 bootstrap.py 构造的类型（组装根）
CONSTRUCT_ONLY_IN_BOOTSTRAP = {
    "Gateway",
    "MemoryStore",
    "SkillStore",
    "JobStore",
    "ToolRegistry",
    "Agent",
    "TurnEndReviewer",
    "BrowserDriver",
    "WriteBudget",
    "ApprovalBroker",
    "ServiceManager",
    "MCPManager",
    "EmbeddingProvider", "VectorIndex", "HybridMemory",
    "DesktopDriver", "WindowsDesktopBackend",
}


def _layer_of(rel: Path) -> str:
    """模块所属层：包名（嵌套文件）或根模块名。"""
    parts = rel.parts
    return parts[0] if len(parts) > 1 else parts[0].removesuffix(".py")


def _package_of(rel: Path) -> str:
    """文件的 python 包路径（相对导入解析基准），如 omni.core / omni。"""
    module_parts = rel.with_suffix("").parts
    if len(module_parts) > 1:
        return "omni." + ".".join(module_parts[:-1])
    return "omni"


def _imported_layers(tree: ast.Module, package: str) -> set[str]:
    """收集本文件 import 的 omni 内部顶层模块名。"""
    layers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("omni."):
                    layers.add(alias.name.split(".")[1])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level and node.level > 0:
                base = package.split(".")
                # level=1 指当前包，每多一级向上退一层
                base = base[: len(base) - (node.level - 1)]
                module = ".".join(base + (module.split(".") if module else []))
            if module.startswith("omni"):
                layers.add(module.split(".")[1])
    return layers


def test_layer_import_rules():
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        layer = _layer_of(rel)
        if layer not in FORBIDDEN_IMPORTS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target in _imported_layers(tree, _package_of(rel)):
            if target in FORBIDDEN_IMPORTS[layer]:
                violations.append(f"{rel}: {layer} 层不允许 import {target}")
    assert not violations, "分层依赖被破坏：\n" + "\n".join(violations)


def test_construction_only_in_bootstrap():
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if str(rel) == "bootstrap.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in CONSTRUCT_ONLY_IN_BOOTSTRAP
            ):
                violations.append(f"{rel}: 直接构造了 {node.func.id}（只允许在 bootstrap.py）")
    assert not violations, "组装根规则被破坏：\n" + "\n".join(violations)
