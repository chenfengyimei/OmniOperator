"""静态源码索引；扫描范围固定为注入根的 src/omni，不读取 config/data/.env。"""
from __future__ import annotations

import ast
from dataclasses import asdict
import hashlib
import tomllib
from importlib.util import resolve_name
from pathlib import Path

from ...config import PROJECT_ROOT
from .schema import FLOWS, MODULES, classify

MAX_FILES = 2000
MAX_FILE_BYTES = 2_000_000
METHODS = frozenset({'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'websocket'})


def source_files(root: Path) -> list[Path]:
    source = root / 'src/omni'
    if source.is_symlink() or source.is_junction():
        raise ValueError('源码根不能为链接')
    files = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix in {'.py', '.ps1', '.html', '.js', '.css'} and '__pycache__' not in p.parts)
    if not files or len(files) > MAX_FILES:
        raise ValueError('源码不存在或文件数超过索引上限')
    for path in files:
        if (not path.resolve().is_relative_to(source.resolve()) or path.is_symlink()
                or any(p.is_junction() or p.is_symlink() for p in path.parents if p.is_relative_to(source))
                or path.stat().st_size > MAX_FILE_BYTES):
            raise ValueError('源码包含链接或超大文件')
    return files


def _imports(tree: ast.AST, module: str, is_package: bool) -> list[str]:
    found = set()
    package = module if is_package else module.rpartition('.')[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith('omni.'))
        elif isinstance(node, ast.ImportFrom):
            try:
                target = resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module or ''
            except (ImportError, ValueError):
                continue
            if target.startswith('omni.'):
                found.add(target)
    return sorted(found)


def _symbols(tree: ast.AST) -> list[dict]:
    found = []
    def visit(node, scope=''):
        for child in getattr(node, 'body', []):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = scope + child.name
                record = {'name': name, 'line': child.lineno, 'kind': 'class' if isinstance(child, ast.ClassDef) else 'function'}
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    record['parameters'] = [arg.arg for arg in [*child.args.posonlyargs, *child.args.args, *child.args.kwonlyargs]]
                    record['annotations'] = {arg.arg: ast.unparse(arg.annotation)[:160] for arg in
                                             [*child.args.posonlyargs, *child.args.args, *child.args.kwonlyargs] if arg.annotation}
                    if child.args.vararg: record['parameters'].append('*' + child.args.vararg.arg)
                    if child.args.kwarg: record['parameters'].append('**' + child.args.kwarg.arg)
                found.append(record)
                visit(child, name + '.')
    visit(tree)
    return found


def _routes(tree: ast.AST, path: str, module_id: str, symbols: list[dict]) -> list[dict]:
    found = []
    by_line = {item['line']: item for item in symbols}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute)
                    or decorator.func.attr not in METHODS or not decorator.args
                    or not isinstance(decorator.args[0], ast.Constant) or not isinstance(decorator.args[0].value, str)):
                continue
            url = decorator.args[0].value
            scope = 'community' if path.startswith('src/omni/plugins/') else 'workbench'
            found.append({'method': decorator.func.attr.upper(), 'path': url, 'source': path,
                          'line': node.lineno, 'handler': by_line[node.lineno]['name'], 'module': module_id,
                          'parameters': by_line[node.lineno].get('parameters', []),
                          'annotations': by_line[node.lineno].get('annotations', {}), 'scope': scope})
    return found


def _tools(tree: ast.AST, path: str, module_id: str) -> list[dict]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'Tool':
            continue
        values = {keyword.arg: keyword.value for keyword in node.keywords}
        name = values.get('name')
        if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
            continue  # 动态插件工具以运行时注册表为准，不能静态捏造工具名。
        risk = values.get('risk')
        human, read_only = values.get('requires_human'), values.get('read_only')
        found.append({'name': name.value, 'source': path, 'line': node.lineno, 'module': module_id,
                      'risk': risk.attr.lower() if isinstance(risk, ast.Attribute) else 'safe',
                      'requires_human': isinstance(human, ast.Constant) and human.value is True,
                      'read_only': isinstance(read_only, ast.Constant) and read_only.value is True})
    return found


def build_index(root: Path = PROJECT_ROOT) -> dict:
    root = Path(root).resolve()
    records, endpoints, declared, warnings, models = [], [], [], [], []
    digest = hashlib.sha256()
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        local = path.relative_to(root / 'src/omni').as_posix()
        owner = classify(local)
        if owner is None:
            warnings.append('未分类源码：' + relative)
        text = path.read_text(encoding='utf-8')
        digest.update(relative.encode()); digest.update(b'\0'); digest.update(text.encode())
        if path.suffix != '.py':
            records.append({'path': relative, 'module': owner.id if owner else 'unclassified', 'python_module': None,
                            'lines': len(text.splitlines()), 'imports': [], 'symbols': [], 'kind': 'asset', 'summary': '平台脚本或界面资源'})
            continue
        tree = ast.parse(text, filename=relative)
        module_name = 'omni.' + local.removesuffix('.py').replace('/', '.')
        if module_name.endswith('.__init__'): module_name = module_name.removesuffix('.__init__')
        symbols = _symbols(tree)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == 'BaseModel' for base in node.bases):
                models.append({'name': node.name, 'source': relative, 'line': node.lineno,
                               'fields': [{'name': field.target.id, 'type': ast.unparse(field.annotation)[:160], 'required': field.value is None}
                                          for field in node.body if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)]})
        record = {'path': relative, 'module': owner.id if owner else 'unclassified', 'python_module': module_name,
                  'lines': len(text.splitlines()), 'imports': _imports(tree, module_name, path.name == '__init__.py'),
                  'symbols': symbols, 'summary': (ast.get_docstring(tree) or '').split('\n', 1)[0][:180]}
        records.append(record)
        endpoints.extend(_routes(tree, relative, record['module'], symbols))
        declared.extend(_tools(tree, relative, record['module']))
    by_path = {row['path']: row for row in records}
    flows = []
    for identity, title, summary, steps in FLOWS:
        chain = []
        for path, symbol in steps:
            source = 'src/omni/' + path
            match = next((s for s in by_path.get(source, {}).get('symbols', []) if s['name'] == symbol), None)
            if match is None: warnings.append(f'调用链定位失效：{path}:{symbol}')
            chain.append({'source': source, 'symbol': symbol, 'line': match['line'] if match else None})
        flows.append({'id': identity, 'title': title, 'summary': summary, 'steps': chain})
    modules = []
    python_owners = {row['python_module']: row['module'] for row in records if row['python_module']}
    for spec in MODULES:
        item = asdict(spec)
        item['files'] = [r['path'] for r in records if r['module'] == spec.id]
        item['lines'] = sum(r['lines'] for r in records if r['module'] == spec.id)
        item['docs'] = ['docs/' + name for name in spec.docs]
        item['tests'] = ['tests/' + name for name in spec.tests]
        item['dependencies'] = sorted({python_owners[target] for record in records if record['module'] == spec.id
                                       for target in record['imports'] if target in python_owners and python_owners[target] != spec.id})
        modules.append(item)
    project = root / 'pyproject.toml'
    entrypoints = tomllib.loads(project.read_text(encoding='utf-8')).get('project', {}).get('scripts', {}) if project.is_file() else {}
    return {'schema': 1, 'source_sha256': digest.hexdigest(), 'modules': modules, 'files': records,
            'endpoints': sorted(endpoints, key=lambda r: (r['scope'], r['path'], r['method'])),
            'declared_tools': sorted(declared, key=lambda r: (r['name'], r['source'])), 'flows': flows, 'request_models': models, 'entrypoints': entrypoints,
            'warnings': warnings, 'notes': ['静态索引不会导入模块或执行工具。', '声明调用链为经源码定位的设计链路，非完整动态调用图。',
                                           '静态工具是声明；启用状态和插件动态工具以本进程注册表为准。']}
