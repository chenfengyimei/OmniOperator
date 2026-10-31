"""tests/ 目录的重复定义守卫（与 scripts/scan_duplicates.py 同一实现）。

分工：`tests/test_arch.py` 已用 AST 锁住 `src/omni` 的同名静默覆盖；
本文件把同样的纪律延伸到 tests/ 目录——2026-11-01 曾发现 5 处测试函数
被同名后定义静默覆盖（复制粘贴残留），被覆盖的测试从未运行过。
CI 跑全量 pytest 时本守卫自动生效，无需单独步骤。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_script = ROOT / "scripts" / "scan_duplicates.py"
_spec = importlib.util.spec_from_file_location("scan_duplicates_guard", _script)
_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scan)


def test_tests_directory_has_no_silent_shadowing():
    findings: list[str] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        findings.extend(_scan.scan(path))
    assert not findings, "tests/ 存在被同名后定义静默覆盖的测试：\n" + "\n".join(findings)
