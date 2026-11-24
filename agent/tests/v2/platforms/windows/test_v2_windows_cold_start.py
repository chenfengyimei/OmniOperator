"""Windows 底座冷启动守卫：init 建库 → doctor 只读体检 → auth 在未启动时给出明确指引。

对应 3f6594eb:docs/v2/platforms/windows/WINDOWS_V2_RUNBOOK.md 的演示路径；这里只验证可离线执行的三步，
不启动 HTTP 服务（服务启动由 test_v2_service_runtime 等覆盖）。
"""

import json
import subprocess
import sys
from pathlib import Path


def run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "omni.composition.cli", "--home", str(home), *args],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_cold_start_init_then_doctor_reports_a_healthy_empty_instance(tmp_path):
    home = tmp_path / "home"
    initialized = run(home, "init")
    assert initialized.returncode == 0, initialized.stderr[-2000:]
    created = json.loads(initialized.stdout)
    assert created["initialized"] is True
    assert created["schema_version"].startswith("v2_")
    assert created["api_version"] == 1

    diagnosed = run(home, "doctor")
    assert diagnosed.returncode == 0, diagnosed.stderr[-2000:]
    report = json.loads(diagnosed.stdout)
    assert report["integrity"] == "ok"
    assert report["foreign_key_errors"] == 0
    assert report["schema"] == created["schema_version"]
    assert report["agents"] == 1
    assert report["objects"]["ok"] is True


def test_auth_before_the_first_serve_explains_the_next_step(tmp_path):
    home = tmp_path / "home"
    assert run(home, "init").returncode == 0
    missing = run(home, "auth")
    assert missing.returncode != 0
    payload = json.loads(missing.stderr or missing.stdout)
    assert payload["error"] == "owner_token_missing"
    assert "serve" in payload["message"]
