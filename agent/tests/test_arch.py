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
    'AgentDirectoryStore',
    "QQMessageStore", "EventLog",
    "PublicationStore", "BilibiliPublisher", "BrowserForms",
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
    "ServiceSupervisor",
    "MCPManager", "CommandJobStore", "CommandJobManager", "AndroidFrameHub",
    "EmbeddingProvider", "VectorIndex", "HybridMemory",
    "DesktopDriver", "WindowsDesktopBackend", "AndroidDriver",
    "PluginHost", "CommunityClient", "SkillInbox",
    "DialogueLog", "SessionLog", "JournalStore", "PersonStore", "MemorySystem",
    "ModeRouter",
}



def violations(source: str, rel: str) -> list[str]:
    """保守静态检查；不执行待检查源码，无法确定的动态导入直接拒绝。"""
    from importlib.util import resolve_name
    tree = ast.parse(source)
    parts = Path(rel).with_suffix("").parts
    layer = parts[0]
    package = ".".join(("omni", *parts[:-1]))
    aliases, imports, issues = {}, set(), []
    domains = {p.name for p in (SRC / "tools").iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = name(node.value)
            return base + "." + node.attr if base else ""
        if isinstance(node, ast.Call):
            function = name(node.func)
            if function in {"getattr", "builtins.getattr"} and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                return name(node.args[0]) + "." + str(node.args[1].value)
            if function in {"importlib.import_module", "__import__", "builtins.__import__"} and node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    return resolve_name(value, package) if value.startswith(".") else value
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
                imports.add(item.name)
        elif isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""
            imports.add(module)
            for item in node.names:
                if item.name == "*":
                    issues.append("禁止星号导入")
                aliases[item.asname or item.name] = module + "." + item.name
                imports.add(module + "." + item.name)
    # 覆盖 G=Gateway、loader=importlib.import_module 等简单别名。
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = name(node.value)
                if value:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = value
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            imports.add(name(node))
        if isinstance(node, ast.Call):
            function = name(node.func)
            if function in {"eval", "exec", "builtins.eval", "builtins.exec"}:
                issues.append("禁止动态执行绕过架构检查")
            if function in {"importlib.import_module", "__import__", "builtins.__import__"}:
                target = name(node)
                if not target:
                    issues.append("动态导入必须使用可静态解析的模块常量")
                else:
                    imports.add(target)
            if rel != "bootstrap.py" and function.rsplit(".", 1)[-1] in CONSTRUCT_ONLY_IN_BOOTSTRAP:
                issues.append("组装根：" + function)
    for target in imports:
        if not target.startswith("omni."):
            continue
        target_parts = target.split(".")
        target_layer = target_parts[1]
        if rel.startswith('channels/web_api/') and target == 'omni.channels.web':
            issues.append('领域路由不得反向依赖 Web 应用入口')
        if rel == 'channels/web_api/schemas.py' and target_layer != 'config':
            issues.append('HTTP 请求契约只允许声明配置类型，不依赖运行态')
        if target_layer == 'plugins' and layer not in {'bootstrap', 'channels', 'plugins'}:
            issues.append(f'插件宿主倒挂：{layer} -> plugins')
        if layer == 'plugins' and target_layer in {'bootstrap', 'channels', 'core', 'llm'}:
            issues.append(f'插件模块不得编排入口/内核/模型：{target}')
        forbidden = FORBIDDEN_IMPORTS.get(layer, set())
        if target_layer in forbidden:
            issues.append(f"分层：{layer} -> {target_layer}")
        if layer in {"config", "safety", "llm"} and target_layer == "system":
            issues.append(f"分层：{layer} -> system")
        if layer == "system" and target_layer not in {"system", "config"}:
            issues.append(f"平台基础设施倒挂：{target}")
        if layer == "memory" and target_layer == "system" and len(target_parts) >= 3 and target_parts[2] not in {"atomic", "filelock", "path_guard", "text_guard"}:
            issues.append(f"memory 只允许共享原子文件/锁/路径安全/文本守卫原语：{target}")
        if layer == "tools" and len(parts) > 2 and parts[1] in domains and target.startswith("omni.tools.") and len(target_parts) > 2:
            domain = target_parts[2]
            if domain in domains and domain != parts[1]:
                issues.append(f"能力域互相引用：{parts[1]} -> {domain}")
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seen = set()
        for node in scope.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [name(d) for d in node.decorator_list]
            # 标准 overload 是类型声明，不占用实现名；第二个真实实现仍必须报错。
            if any(d in {'typing.overload','typing_extensions.overload'} for d in decorators):
                continue
            exempt = any(d.endswith((".setter", ".deleter", ".getter")) for d in decorators)
            if node.name in seen and not exempt:
                issues.append("重复定义：" + node.name)
            seen.add(node.name)
    return sorted(set(issues))


def test_guard_accepts_overload_declarations_but_rejects_duplicate_implementation():
    declarations='from typing import overload\n@overload\ndef f(x: int) -> int: ...\n@overload\ndef f(x: str) -> str: ...\ndef f(x): return x\n'
    assert not violations(declarations,'tools/android/protocol.py')
    assert any('重复定义' in item for item in violations(declarations+'def f(x): return x\n','tools/android/protocol.py'))


def test_source_architecture():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        found.extend(f"{rel}: {item}" for item in violations(path.read_text(encoding="utf-8"), rel))
    assert not found, "\n".join(found)


import pytest


@pytest.mark.parametrize("rel,source", [
    ("safety/x.py", "from omni import llm"),
    ("safety/x.py", "import importlib as i; i.import_module('omni.llm.gateway')"),
    ("safety/x.py", "from importlib import import_module as load; load('omni.llm.gateway')"),
    ("safety/x.py", "__import__('omni.llm.gateway')"),
    ("safety/x.py", "from ..llm import gateway"),
    ("memory/x.py", "import omni.core.agent"),
    ("llm/x.py", "from omni.memory.store import MemoryStore"),
    ("tools/bilibili/x.py", "from ..browser.driver import BrowserDriver"),
    ("tools/browser/x.py", "import omni.tools.bilibili.api"),
    ("channels/x.py", "from omni.llm.gateway import Gateway as G; G(None)"),
    ("channels/x.py", "import omni.llm.gateway as g; g.Gateway(None)"),
    ("channels/x.py", "from omni.llm.gateway import Gateway; G=Gateway; G(None)"),
    ("channels/x.py", "import omni.llm.gateway as g; getattr(g,'Gateway')(None)"),
    ("channels/x.py", "import importlib; importlib.import_module('omni.llm.gateway').Gateway(None)"),
    ("tools/x.py", "import importlib; importlib.import_module(some_name)"),
    ("memory/x.py", "from omni.system import services"),
    ("system/x.py", "from omni.core.agent import Agent"),
    ("tools/x.py", "exec(source)"),
    ("tools/x.py", "from omni.config import *"),
    ("core/x.py", "def f(): pass\ndef f(): pass"),
    ("memory/x.py", "from omni.plugins.host import PluginHost"),
    ("plugins/x.py", "from omni.core.agent import Agent"),
    ("plugins/x.py", "from omni.llm.gateway import Gateway"),
    ("channels/web_api/conversation.py", "from omni.channels.web import create_app"),
    ("channels/web_api/schemas.py", "from omni.memory.store import MemoryStore"),
])
def test_architecture_guard_catches_bypasses(rel, source):
    assert violations(source, rel)


def test_guard_allows_injected_protocols_and_bootstrap_construction():
    assert not violations("from omni.tools.browser.driver import BrowserDriver\nx=BrowserDriver()", "bootstrap.py")
    assert not violations("from typing import Protocol\nfrom ..result import ToolResult\nclass Port(Protocol): pass", "tools/bilibili/x.py")
