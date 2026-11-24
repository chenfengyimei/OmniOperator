"""Directory moves must retain executable discovery and one owner per test fixture."""

import ast
import json
import runpy

import pytest
from omni_test_support.paths import PRODUCT_ROOT, REPOSITORY_ROOT, TEST_ROOT


def test_manifest_targets_resolve_and_platform_implementations_have_one_home():
    manifest = json.loads((REPOSITORY_ROOT / "docs/v/layout.json").read_text(encoding="utf-8"))
    targets = [row["new"] for row in manifest["moves"]]
    assert len(targets) == len(set(targets))
    assert all((REPOSITORY_ROOT / name).is_file() for name in targets)
    platform = PRODUCT_ROOT / "src/omni/adapters/platform"
    assert {p.name for p in platform.glob("*.py")} == {"__init__.py"}
    assert all((platform / name / "__init__.py").is_file() for name in ["desktop", "services", "releases", "runtime"])
    assert (platform / "desktop/linux/__init__.py").is_file()


def test_v2_tests_do_not_import_other_collected_test_modules():
    violations = []
    for path in (TEST_ROOT / "v2").rglob("test_v2_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1].startswith("test_v2_"):
                violations.append((str(path), node.module))
            if isinstance(node, ast.Import):
                violations.extend(
                    (str(path), alias.name) for alias in node.names if alias.name.split(".")[-1].startswith("test_v2_")
                )
    assert violations == []


def test_ci_discovers_nested_files_and_refuses_an_empty_suite(tmp_path):
    discover = runpy.run_path(str(REPOSITORY_ROOT / "scripts/ci_v2.py"))["test_files"]
    with pytest.raises(ValueError, match="empty"):
        discover(tmp_path)
    nested = tmp_path / "agent/tests/v2/platforms/windows/test_v2_probe.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("def test_probe(): pass\n", encoding="utf-8")
    assert discover(tmp_path) == [str(nested.relative_to(tmp_path))]
