"""独立产品目录、旧命令兼容与运行数据位置的回归契约。"""

from pathlib import Path
import os
import subprocess
import sys
import tomllib

from omni.config import (
    APP_ROOT,
    PROJECT_ROOT,
    CONFIG_PATH,
    DATA_DIR,
    ENV_PATH,
    SKILLS_DIR,
    application_root,
    source_root,
)
from omni.system.catalog import build_index
from omni.system.catalog.scan import source_files
from omni.system.provenance import source_fingerprint


def test_product_and_runtime_roots_are_separate():
    assert APP_ROOT == Path(__file__).resolve().parents[1]
    assert PROJECT_ROOT == APP_ROOT.parent and APP_ROOT.name == "agent"
    assert CONFIG_PATH == PROJECT_ROOT / "config/config.yaml"
    assert DATA_DIR == PROJECT_ROOT / "data" and ENV_PATH == PROJECT_ROOT / ".env"
    assert SKILLS_DIR == PROJECT_ROOT / "skills"
    assert source_root(PROJECT_ROOT) == APP_ROOT / "src/omni"
    assert source_root(APP_ROOT) == APP_ROOT / "src/omni"
    assert (APP_ROOT / "config/config.example.yaml").is_file()
    assert (APP_ROOT / "LICENSE").read_bytes() == (PROJECT_ROOT / "LICENSE").read_bytes()


def test_workspace_resolves_product_and_keeps_default_and_explicit_entrypoints():
    workspace = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    product = tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert workspace["tool"]["uv"]["workspace"]["members"] == ["agent"]
    assert workspace["tool"]["uv"]["sources"]["omnioperator"] == {"workspace": True}
    assert workspace["tool"]["pytest"]["ini_options"]["testpaths"] == ["agent/tests"]
    assert product["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/omni"]
    assert product["project"]["scripts"] == {
        "omni": "omni.composition.cli:main",
        "omni-web": "omni.composition.cli:web_main",
        "omni-qq": "omni.channels.qq:main",
        "omni-heartbeat": "omni.channels.heartbeat:main",
        "omni-v1": "omni.channels.cli:main",
        "omni-web-v1": "omni.channels.web:main",
        "omni-v2": "omni.composition.cli:main",
        "omni-web-v2": "omni.composition.cli:web_main",
    }
    index = build_index(PROJECT_ROOT)
    assert not index["warnings"] and index["entrypoints"] == product["project"]["scripts"]
    assert all(row["path"].startswith(("agent/src/omni/", "web/")) for row in index["files"])
    assert all(path.startswith("agent/tests/") for row in index["modules"] for path in row["tests"])


def test_new_source_layout_wins_and_legacy_injected_layout_still_works(tmp_path):
    legacy = tmp_path / "src/omni"
    legacy.mkdir(parents=True)
    (legacy / "config.py").write_text("legacy = True", encoding="utf-8")
    assert application_root(tmp_path) == tmp_path
    assert source_files(tmp_path) == [legacy / "config.py"]
    product = tmp_path / "agent/src/omni"
    product.mkdir(parents=True)
    (product / "config.py").write_text("product = True", encoding="utf-8")
    assert application_root(tmp_path) == tmp_path / "agent"
    assert source_files(tmp_path) == [product / "config.py"]
    assert len(build_index(tmp_path)["files"]) == 1


def test_product_metadata_affects_fingerprint_but_private_files_do_not(tmp_path):
    source = tmp_path / "agent/src/omni"
    source.mkdir(parents=True)
    (source / "config.py").write_text("x = 1", encoding="utf-8")
    metadata = tmp_path / "agent/pyproject.toml"
    metadata.write_text('name="before"', encoding="utf-8")
    before = source_fingerprint(tmp_path)
    (tmp_path / ".env").write_text("SECRET_CANARY", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/private.py").write_text("PRIVATE_CANARY", encoding="utf-8")
    assert source_fingerprint(tmp_path) == before
    metadata.write_text('name="after"', encoding="utf-8")
    assert source_fingerprint(tmp_path) != before


def test_old_script_entrypoint_forwards_and_uses_product_source():
    env = {**os.environ, "PYTHONUTF8": "1"}
    for script in (PROJECT_ROOT / "scripts/project_index.py", APP_ROOT / "scripts/project_index.py"):
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        assert "--check" in result.stdout
