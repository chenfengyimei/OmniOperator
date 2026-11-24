"""静态源码索引；扫描范围为本地 Agent 的源码包（兼容旧布局），不读取 config/data/.env。"""

from __future__ import annotations

import ast
import hashlib
import tomllib
from dataclasses import asdict
from importlib.util import resolve_name
from pathlib import Path

from ...config import PROJECT_ROOT, application_root, source_root, web_root
from .schema import FLOWS, MODULES, classify, document_path, file_version

MAX_FILES = 2000
MAX_FILE_BYTES = 2_000_000
METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "websocket"})


def source_files(root: Path) -> list[Path]:
    source = source_root(root)
    if source.is_symlink() or source.is_junction() or not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("源码根不能为链接")
    assets = web_root(root)
    roots = [source] + ([assets] if assets.exists() and not assets.is_relative_to(source) else [])
    files = []
    for base in roots:
        if base.is_symlink() or base.is_junction() or not base.resolve().is_relative_to(root.resolve()):
            raise ValueError("源码根不能为链接")
        for path in base.rglob("*"):
            if (
                not path.is_file()
                or path.suffix not in {".py", ".ps1", ".cs", ".swift", ".c", ".h", ".html", ".js", ".css"}
                or any(part in {"__pycache__", "node_modules", ".build", ".swiftpm"} for part in path.parts)
            ):
                continue
            if (
                not path.resolve().is_relative_to(base.resolve())
                or path.is_symlink()
                or any(p.is_junction() or p.is_symlink() for p in path.parents if p.is_relative_to(base))
                or path.stat().st_size > MAX_FILE_BYTES
            ):
                raise ValueError("源码包含链接或超大文件")
            files.append(path)
    files = sorted(set(files))
    if not files or len(files) > MAX_FILES:
        raise ValueError("源码不存在或文件数超过索引上限")
    return files


def _imports(tree: ast.AST, module: str, is_package: bool) -> list[str]:
    found = set()
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("omni."))
        elif isinstance(node, ast.ImportFrom):
            try:
                target = (
                    resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""
                )
            except (ImportError, ValueError):
                continue
            if target.startswith("omni."):
                found.add(target)
    return sorted(found)


def _symbols(tree: ast.AST) -> list[dict]:
    found = []

    def visit(node, scope=""):
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = scope + child.name
                record = {
                    "name": name,
                    "line": child.lineno,
                    "kind": "class" if isinstance(child, ast.ClassDef) else "function",
                }
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    record["parameters"] = [
                        arg.arg for arg in [*child.args.posonlyargs, *child.args.args, *child.args.kwonlyargs]
                    ]
                    record["annotations"] = {
                        arg.arg: ast.unparse(arg.annotation)[:160]
                        for arg in [*child.args.posonlyargs, *child.args.args, *child.args.kwonlyargs]
                        if arg.annotation
                    }
                    if child.args.vararg:
                        record["parameters"].append("*" + child.args.vararg.arg)
                    if child.args.kwarg:
                        record["parameters"].append("**" + child.args.kwarg.arg)
                found.append(record)
                visit(child, name + ".")

    visit(tree)
    return found


def _router_prefixes(tree: ast.AST) -> dict[str, str]:
    prefixes = {}
    for assignment in ast.walk(tree):
        if not isinstance(assignment, ast.Assign) or not isinstance(assignment.value, ast.Call):
            continue
        call = assignment.value
        if not isinstance(call.func, ast.Name) or call.func.id != "APIRouter":
            continue
        prefix = next(
            (
                kw.value.value
                for kw in call.keywords
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
            ),
            "",
        )
        for target in assignment.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _feature_mounts(tree: ast.Module, prefixes: dict[str, str]) -> dict[str, str]:
    """Read the V2 parent's include_router(factory(...)) mounts without importing handlers."""
    factories = {
        alias.asname or alias.name: f"omni.adapters.{node.module}.{alias.name}"
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1 and (node.module or "").startswith("http_routes.")
        for alias in node.names
    }
    mounts = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        parent = node.func.value
        if node.func.attr != "include_router" or not isinstance(parent, ast.Name) or parent.id not in prefixes:
            continue
        child = node.args[0] if node.args else None
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in factories:
            mounts[factories[child.func.id]] = prefixes[parent.id]
    return mounts


def _routes(tree: ast.AST, path: str, module_id: str, symbols: list[dict], prefixes: dict[str, str]) -> list[dict]:
    found = []
    by_line = {item["line"]: item for item in symbols}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                not isinstance(decorator, ast.Call)
                or not isinstance(decorator.func, ast.Attribute)
                or decorator.func.attr not in METHODS
                or not decorator.args
                or not isinstance(decorator.args[0], ast.Constant)
                or not isinstance(decorator.args[0].value, str)
            ):
                continue
            owner = decorator.func.value
            url = (
                prefixes.get(owner.id, "") + decorator.args[0].value
                if isinstance(owner, ast.Name)
                else decorator.args[0].value
            )
            scope = "v2" if "/omni/adapters/" in path else "community" if "/omni/plugins/" in path else "workbench"
            found.append(
                {
                    "method": decorator.func.attr.upper(),
                    "path": url,
                    "source": path,
                    "line": node.lineno,
                    "handler": by_line[node.lineno]["name"],
                    "module": module_id,
                    "parameters": by_line[node.lineno].get("parameters", []),
                    "annotations": by_line[node.lineno].get("annotations", {}),
                    "scope": scope,
                }
            )
    return found


def _tools(tree: ast.AST, path: str, module_id: str) -> list[dict]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "Tool":
            continue
        values = {keyword.arg: keyword.value for keyword in node.keywords}
        name = values.get("name")
        if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
            continue  # 动态插件工具以运行时注册表为准，不能静态捏造工具名。
        risk = values.get("risk")
        human, read_only = values.get("requires_human"), values.get("read_only")
        found.append(
            {
                "name": name.value,
                "source": path,
                "line": node.lineno,
                "module": module_id,
                "risk": risk.attr.lower() if isinstance(risk, ast.Attribute) else "safe",
                "requires_human": isinstance(human, ast.Constant) and human.value is True,
                "read_only": isinstance(read_only, ast.Constant) and read_only.value is True,
            }
        )
    return found


def _required_field(value: ast.expr | None) -> bool:
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Field":
        if any(kw.arg == "default_factory" for kw in value.keywords):
            return False
        value = value.args[0] if value.args else next((kw.value for kw in value.keywords if kw.arg == "default"), None)
    return value is None or isinstance(value, ast.Constant) and value.value is Ellipsis


def _request_models(tree: ast.Module, relative: str) -> list[dict]:
    # Local subclasses inherit their parent's fields. V2 imports the common strict base.
    fields_by_class: dict[str, dict[str, dict]] = {"BaseModel": {}}
    if any(isinstance(node, ast.ImportFrom) and any(a.name == "StrictInput" for a in node.names) for node in tree.body):
        fields_by_class["StrictInput"] = {}
    models = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        parents = [base.id for base in node.bases if isinstance(base, ast.Name) and base.id in fields_by_class]
        if not parents:
            continue
        fields = {key: field for parent in reversed(parents) for key, field in fields_by_class[parent].items()}
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            name = item.target.id
            fields[name] = {
                "name": name,
                "type": ast.unparse(item.annotation)[:160],
                "required": _required_field(item.value),
            }
        fields_by_class[node.name] = fields
        models.append({"name": node.name, "source": relative, "line": node.lineno, "fields": list(fields.values())})
    return models


def build_index(root: Path = PROJECT_ROOT) -> dict:
    root = Path(root).resolve()
    app = application_root(root)
    source = source_root(root)
    source_prefix = source.relative_to(root).as_posix() + "/"
    test_prefix = (app / "tests").relative_to(root).as_posix() + "/"
    records, endpoints, declared, warnings, models = [], [], [], [], []
    feature_mounts: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        local = (
            path.relative_to(source).as_posix()
            if path.is_relative_to(source)
            else "web/" + path.relative_to(web_root(root)).as_posix()
        )
        if local.startswith("channels/web_static/"):
            local = "web/" + local.removeprefix("channels/web_static/")
        owner = classify(local)
        if owner is None:
            warnings.append("未分类源码：" + relative)
        text = path.read_text(encoding="utf-8")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(text.encode())
        if path.suffix != ".py":
            records.append(
                {
                    "path": relative,
                    "version": file_version(local),
                    "module": owner.id if owner else "unclassified",
                    "python_module": None,
                    "lines": len(text.splitlines()),
                    "imports": [],
                    "symbols": [],
                    "kind": "asset",
                    "summary": "原生源码、平台脚本或界面资源",
                }
            )
            continue
        tree = ast.parse(text, filename=relative)
        module_name = "omni." + local.removesuffix(".py").replace("/", ".")
        if module_name.endswith(".__init__"):
            module_name = module_name.removesuffix(".__init__")
        symbols = _symbols(tree)
        models.extend(_request_models(tree, relative))
        record = {
            "path": relative,
            "module": owner.id if owner else "unclassified",
            "version": file_version(local),
            "python_module": module_name,
            "lines": len(text.splitlines()),
            "imports": _imports(tree, module_name, path.name == "__init__.py"),
            "symbols": symbols,
            "summary": (ast.get_docstring(tree) or "").split("\n", 1)[0][:180],
        }
        records.append(record)
        prefixes = _router_prefixes(tree)
        if module_name == "omni.adapters.http":
            feature_mounts = _feature_mounts(tree, prefixes)
        endpoints.extend(_routes(tree, relative, record["module"], symbols, prefixes))
        declared.extend(_tools(tree, relative, record["module"]))
    by_path = {row["path"]: row for row in records}
    for route in endpoints:
        if "/adapters/http_routes/" in route["source"]:
            module = by_path[route["source"]]["python_module"]
            factory = module + "." + route["handler"].split(".")[0]
            if factory in feature_mounts:
                route["path"] = feature_mounts[factory] + route["path"]
            else:
                warnings.append("功能路由未挂载：" + factory)
    flows = []
    for identity, title, summary, steps in FLOWS:
        chain = []
        for path, symbol in steps:
            source = source_prefix + path
            match = next((s for s in by_path.get(source, {}).get("symbols", []) if s["name"] == symbol), None)
            if match is None:
                warnings.append(f"调用链定位失效：{path}:{symbol}")
            chain.append({"source": source, "symbol": symbol, "line": match["line"] if match else None})
        flows.append({"id": identity, "title": title, "summary": summary, "steps": chain})
    modules = []
    python_owners = {row["python_module"]: row["module"] for row in records if row["python_module"]}
    for spec in MODULES:
        item = asdict(spec)
        item["files"] = [r["path"] for r in records if r["module"] == spec.id]
        item["lines"] = sum(r["lines"] for r in records if r["module"] == spec.id)
        item["docs"] = [document_path(name) for name in spec.docs]
        item["tests"] = [test_prefix + name for name in spec.tests]
        item["dependencies"] = sorted(
            {
                python_owners[target]
                for record in records
                if record["module"] == spec.id
                for target in record["imports"]
                if target in python_owners and python_owners[target] != spec.id
            }
        )
        modules.append(item)
    project = app / "pyproject.toml"
    entrypoints = (
        tomllib.loads(project.read_text(encoding="utf-8")).get("project", {}).get("scripts", {})
        if project.is_file()
        else {}
    )
    return {
        "schema": 1,
        "source_sha256": digest.hexdigest(),
        "modules": modules,
        "files": records,
        "endpoints": sorted(endpoints, key=lambda r: (r["scope"], r["path"], r["method"])),
        "declared_tools": sorted(declared, key=lambda r: (r["name"], r["source"])),
        "flows": flows,
        "request_models": models,
        "entrypoints": entrypoints,
        "warnings": warnings,
        "notes": [
            "静态索引不会导入模块或执行工具。",
            "声明调用链为经源码定位的设计链路，非完整动态调用图。",
            "静态工具是声明；启用状态和插件动态工具以本进程注册表为准。",
        ],
    }
