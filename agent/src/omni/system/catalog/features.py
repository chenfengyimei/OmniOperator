"""Developer navigation from an explicit manifest. Resolve Python locations from AST only."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

GROUPS = {
    "entry": "从这里读",
    "domain": "领域规则",
    "application": "应用服务",
    "adapters": "适配器与仓储",
    "composition": "组装点",
    "ui": "界面",
    "contracts": "合同与手册",
    "examples": "样例",
    "tests": "专项测试",
}
# Only these repository trees contain development navigation targets.
ROOTS = ("agent/src/", "agent/tests/", "agent/scripts/", "agent/examples/", "scripts/", "web/", "docs/v/reference/")


@dataclass(frozen=True)
class Location:
    path: str
    symbol: str
    line: int | None

    def markdown(self) -> str:
        label = self.path + ("#" + self.symbol if self.symbol else "")
        anchor = f"#L{self.line}" if self.line else ""
        return f"[{label}](../../../{self.path}{anchor})"


@dataclass
class Feature:
    id: str
    name: str
    version: str
    purpose: str
    impact: str
    aliases: list[str]
    pages: list[str]
    related: list[str]
    refs: dict[str, list[Location]]

    def keys(self) -> set[str]:
        return {term.casefold() for term in (self.id, self.name, *self.aliases, *self.pages)}


def _strings(row: dict, key: str) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
        raise ValueError(f"{row.get('id')} 的 {key} 应为文本数组")
    if len(set(value)) != len(value):
        raise ValueError(f"{row.get('id')} 的 {key} 有重复项")
    return value


def _locations(file: Path) -> dict[str, tuple[int, str]]:
    """Qualified definitions and unambiguous local bindings, including inside try/with blocks."""
    found: dict[str, tuple[int, str]] = {}
    ambiguous = set()

    def visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = scope + node.name
            found[name] = (node.lineno, "class" if isinstance(node, ast.ClassDef) else "function")
            scope = name + "."
        elif scope and isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    key = scope + target.id
                    if key in found:
                        ambiguous.add(key)
                    found[key] = (node.lineno, "binding")
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(ast.parse(file.read_text(encoding="utf-8"), filename=str(file)), "")
    return {name: location for name, location in found.items() if name not in ambiguous}


def load_features(root: Path) -> list[Feature]:
    root = root.resolve()
    rows = json.loads((root / "docs/v/features.json").read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("功能清单应为非空数组")
    features = []
    ids: set[str] = set()
    pages: set[str] = set()
    symbols: dict[str, dict[str, tuple[int, str]]] = {}

    def locate(ref: str, group: str) -> Location:
        path, sep, symbol = ref.partition("#")
        if not path.startswith(ROOTS) or "\\" in path or any(p in {"", ".", ".."} for p in path.split("/")):
            raise ValueError(f"功能路径不在开发目录内：{ref}")
        file = root / path
        if file.resolve() != file or not file.is_file():
            raise ValueError(f"功能路径缺失或经过链接：{ref}")
        if group == "tests" and not (path.startswith("agent/tests/") and file.match("test_*.py")):
            raise ValueError(f"无效的 pytest 文件：{ref}")
        if not sep:
            return Location(path, "", None)
        if file.suffix != ".py" or (group == "tests" and not symbol.split(".")[-1].startswith("test_")):
            raise ValueError(f"无效的符号引用：{ref}")
        if path not in symbols:
            symbols[path] = _locations(file)
        if symbol not in symbols[path]:
            raise ValueError(f"找不到 Python 符号：{ref}")
        line, kind = symbols[path][symbol]
        if group == "tests" and kind != "function":
            raise ValueError(f"pytest 目标必须是测试函数：{ref}")
        return Location(path, symbol, line)

    for row in rows:
        fields = ("id", "name", "version", "purpose", "impact")
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k].strip() for k in fields):
            raise ValueError("功能记录缺少名称、版本、用途或影响说明")
        identity = row["id"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identity) or identity in ids:
            raise ValueError(f"无效或重复的功能 ID：{identity}")
        ids.add(identity)
        aliases, owned, related = (_strings(row, key) for key in ("aliases", "pages", "related"))
        if pages.intersection(owned):
            raise ValueError(f"工作台页面重复登记：{identity}")
        pages.update(owned)
        refs = row.get("refs")
        if not isinstance(refs, dict) or set(refs) - GROUPS.keys() or not refs.get("entry") or not refs.get("tests"):
            raise ValueError(f"{identity} 需要有效的入口与测试分组")
        resolved = {key: [locate(ref, key) for ref in _strings(refs, key)] for key in refs}
        features.append(
            Feature(
                id=identity,
                name=row["name"],
                version=row["version"],
                purpose=row["purpose"],
                impact=row["impact"],
                aliases=aliases,
                pages=owned,
                related=related,
                refs=resolved,
            )
        )
    for feature in features:
        if set(feature.related) - ids:
            raise ValueError(f"{feature.id} 引用了未知的相关功能")
    nav = (root / "web/ui/v2/index.html").read_text(encoding="utf-8")
    missing = pages ^ set(re.findall(r'data-page="([a-z-]+)"', nav))
    if missing:
        raise ValueError("功能清单与工作台页面不一致：" + ", ".join(sorted(missing)))
    return features


def _section(feature: Feature, features: dict[str, Feature], *, markdown: bool) -> str:
    lines = [f"## {feature.name} ({feature.id} · {feature.version})", "", feature.purpose, ""]
    if feature.pages:
        lines.extend(["工作台页面 " + "、".join(feature.pages), ""])
    for key, title in GROUPS.items():
        refs = feature.refs.get(key, [])
        if refs:
            locations = [
                ref.markdown() if markdown else ref.path + (f":{ref.line} ({ref.symbol})" if ref.line else "")
                for ref in refs
            ]
            lines.extend([f"**{title}**", "", *("- " + location for location in locations), ""])
    targets = [ref.path + ("::" + ref.symbol.replace(".", "::") if ref.symbol else "") for ref in feature.refs["tests"]]
    lines.extend([f"**改动影响**　{feature.impact}", ""])
    if feature.related:
        lines.extend(["**相关功能**", ""])
        for key in feature.related:
            other = features[key]
            lines.append(f"- {other.name} ({key}, {other.version})　{other.purpose}")
        lines.append("")
    lines.extend(["**专项命令**", "", "```powershell", "uv run --no-sync pytest -q -n 0 " + " ".join(targets), "```"])
    return "\n".join(lines)


def render_features(features: list[Feature]) -> str:
    by_id = {f.id: f for f in features}
    lines = [
        "# 功能开发地图",
        "",
        "在仓库根运行 `uv run --no-sync python scripts/project_index.py --feature 插件`。"
        "可输入功能 ID、页面名、别名或开发任务。编辑 `docs/v/features.json` 后重新生成本页。",
        "",
        "| 功能 | 归属 | 常用检索词 |",
        "|---|---|---|",
    ]
    for f in features:
        lines.append(f"| [{f.name}](#feature-{f.id}) | {f.version} | {'、'.join(f.aliases)} |")
    for f in features:
        lines.extend(["", f'<a id="feature-{f.id}"></a>', "", _section(f, by_id, markdown=True)])
    return "\n".join(lines) + "\n"


def query(features: list[Feature], term: str) -> tuple[str, bool]:
    term = term.strip().casefold()
    by_id = {f.id: f for f in features}
    candidates = [f for f in features if term in f.keys()]
    if term in by_id:
        candidates = [by_id[term]]
    if not candidates and term:
        scores = [(max((len(k) for k in f.keys() if k in term), default=0), f) for f in features]
        best = max(score for score, _ in scores)
        candidates = (
            [f for score, f in scores if score == best]
            if best
            else [f for f in features if any(term in k for k in f.keys())]
        )
    if len(candidates) == 1:
        return _section(candidates[0], by_id, markdown=False), True
    label = "匹配多个功能，请使用 ID" if candidates else "没有匹配的功能，可用 ID"
    return label + "\n" + "\n".join(f"  {f.id}  {f.name}" for f in candidates or features), False
