"""一次性收尾扫描：同名函数/类在同一作用域内的静默覆盖（历史教训登记项）。

用法：uv run --no-sync python scripts/scan_duplicates.py
退出码 0 = 无发现；1 = 有重复定义（输出逐条定位）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "src" / "omni", ROOT / "tests"]

# 装饰器豁免（与 tests/test_arch.py 的重复定义规则同口径）
_EXEMPT_SUFFIXES = (".setter", ".deleter", ".getter", "overload")


def scan(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return [f"{path}: 解析失败 {exc}"]
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seen: dict[str, int] = {}
        for node in scope.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = []
            for deco in node.decorator_list:
                if isinstance(deco, ast.Name):
                    decorators.append(deco.id)
                elif isinstance(deco, ast.Attribute):
                    decorators.append(ast.unparse(deco))
            if any(d.endswith(_EXEMPT_SUFFIXES) for d in decorators):
                continue
            if node.name in seen:
                rel = path.relative_to(ROOT).as_posix()
                findings.append(f"{rel}: 重复定义 {node.name}（第 {seen[node.name]} 行被第 {node.lineno} 行静默覆盖）")
            else:
                seen[node.name] = node.lineno
    return findings


def main() -> int:
    all_findings: list[str] = []
    for target in TARGETS:
        for path in sorted(target.rglob("*.py")):
            all_findings.extend(scan(path))
    if not all_findings:
        print("无同名静默覆盖。")
        return 0
    for line in all_findings:
        print(line)
    print(f"共 {len(all_findings)} 处。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
