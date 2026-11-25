"""Developer navigation remains static, searchable, and linked to the live API contract."""

import json
import os
import re
import subprocess
import sys

import pytest
from omni_test_support.paths import REPOSITORY_ROOT

from omni.composition.http import http_app
from omni.system.catalog import build_index
from omni.system.catalog.features import load_features, query, render_features


@pytest.fixture(scope="module")
def features():
    return load_features(REPOSITORY_ROOT)


@pytest.mark.parametrize(
    ("term", "feature", "entry"),
    [
        ("插件", "extensions", "extensions_router.extension_authorization"),
        ("修改插件授权", "extensions", "extensions_router.extension_authorization"),
        ("修改技能回滚", "skills", "skills_router.skill_rollback"),
        ("排查安卓截图", "android", "android_router.android_capture"),
        ("ANDROID", "android", "AndroidService.capture"),
        ("omni-qq", "v1-qq", "channels/qq.py"),
    ],
)
def test_task_leads_to_entry_composition_and_runnable_tests(features, term, feature, entry):
    answer, matched = query(features, term)
    assert matched and feature in answer.splitlines()[0] and entry in answer
    assert "组装点" in answer and "改动影响" in answer and "uv run --no-sync pytest -q -n 0 agent/tests/" in answer
    if term == "插件":
        assert all(key in answer for key in ("v1-plugins", "skills", "mcp"))


def test_missing_and_ambiguous_queries_have_candidates(features):
    absent, matched = query(features, "不存在的火星需求")
    assert not matched and "可用 ID" in absent and "android" in absent
    multiple, matched = query(features, "截图")
    assert not matched and "android" in multiple and "desktop" in multiple


def test_forwarding_cli_prints_utf8_and_finds_the_actual_wiring_line():
    env = os.environ.copy()
    env.pop("PYTHONIOENCODING", None)
    query_run = subprocess.run(
        [sys.executable, "scripts/project_index.py", "--feature", "修改插件授权"],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert "可执行插件" in query_run.stdout
    source = "agent/src/omni/composition/features/extensions.py"
    match = re.search(re.escape(source) + r":(\d+) \(install_extensions\)", query_run.stdout)
    assert match
    line = (REPOSITORY_ROOT / source).read_text(encoding="utf-8").splitlines()[int(match[1]) - 1]
    assert line.startswith("async def install_extensions(")


def test_generated_page_links_to_actual_symbols_and_covers_all_workbench_pages(features):
    page = render_features(features)
    assert "extensions.py#L" in page and page == render_features(load_features(REPOSITORY_ROOT))
    inventory = (REPOSITORY_ROOT / "docs/v/reference/V2_BUSINESS_INVENTORY.md").read_text(encoding="utf-8")
    assert {p for f in features for p in f.pages} == set(re.findall(r"^\| `([a-z-]+)` \|", inventory, re.M))


@pytest.fixture
def manifest(tmp_path):
    directory = tmp_path / "docs/v"
    directory.mkdir(parents=True)
    root = tmp_path / "agent/tests"
    root.mkdir(parents=True)
    (root / "test_static.py").write_text("raise RuntimeError('must not import')\ndef test_marker(): pass\n")
    ui = tmp_path / "web/ui/v2"
    ui.mkdir(parents=True)
    (ui / "index.html").write_text('<a data-page="fixture">')
    row = dict(
        id="fixture",
        name="fixture",
        aliases=[],
        version="V2",
        purpose="fixture",
        impact="fixture",
        related=[],
        pages=["fixture"],
        refs={"entry": ["agent/tests/test_static.py"], "tests": ["agent/tests/test_static.py#test_marker"]},
    )
    return directory / "features.json", row


@pytest.mark.parametrize("defect", ["none", "duplicate", "missing", "symbol", "test", "archive", "page"])
def test_manifest_validation_never_imports_targets(manifest, tmp_path, defect):
    path, row = manifest
    rows = [row]
    if defect == "duplicate":
        rows.append(row)
    elif defect == "page":
        row["pages"] = []
    elif defect != "none":
        group, ref = {
            "missing": ("entry", "agent/tests/absent.py"),
            "symbol": ("entry", "agent/tests/test_static.py#missing"),
            "test": ("tests", "web/ui/v2/index.html"),
            "archive": ("entry", "docs/v/reference/../../temp/secret.md"),
        }[defect]
        row["refs"][group] = [ref]
    path.write_text(json.dumps(rows), encoding="utf-8")
    if defect == "none":
        [feature] = load_features(tmp_path)
        assert feature.refs["tests"][0].line == 2
    else:
        with pytest.raises(ValueError):
            load_features(tmp_path)


def test_extracted_route_sources_methods_and_request_models_match_openapi():
    index = build_index(REPOSITORY_ROOT)
    api = http_app().openapi()
    extracted = {(r["path"], r["method"].lower()) for r in index["endpoints"] if "/http_routes/" in r["source"]}
    expected = {
        (path, method)
        for path, methods in api["paths"].items()
        for method in methods
        if set(path.split("/"))
        & {
            "skills",
            "extensions",
            "mcp",
            "android",
            "media",
            "schedules",
            "qq",
            "bilibili",
            "desktop",
            "deliveries",
            "terminals",
            "terminal-reviews",
            "terminal-operations",
            "action-reviews",
            "actions",
            "documents",
            "people",
            "persona-growth",
            "memory",
            "reflections",
            "maintenance",
            "images",
        }
    }
    assert extracted == expected and len(expected) == 181
    for schema in ["SkillImportInput", "SkillSaveInput", "SkillStateInput", "SkillRollbackInput"]:
        model = next(m for m in index["request_models"] if m["name"] == schema)
        assert model["source"].endswith("http_routes/skills.py")
        assert {f["name"] for f in model["fields"] if f["required"]} == set(
            api["components"]["schemas"][schema]["required"]
        )
    versions = {f["path"]: f["version"] for f in index["files"]}
    assert versions["agent/src/omni/channels/qq.py"] == "V1"
    assert versions["agent/src/omni/application/qq.py"] == "V2"
    assert versions["agent/src/omni/system/catalog/scan.py"] == "共享"
