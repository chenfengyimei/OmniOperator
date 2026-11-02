"""安装脚本守卫：PS 5.1 兼容、BOM、启动纪律与配对文件契约（静态检查，不执行脚本）。

背景：install.ps1 / start.ps1 / installer_lib.ps1 是装机与启动的辅助脚本，坏了就是
装不上/起不来；PowerShell 脚本没有 pytest 之外的测试基建，这里至少把已知平台坑钉死：
- PowerShell 5.1 不支持 && / ||（本仓库实测过的坑）；
- PS 5.1 按 ANSI 解析无 BOM 的 .ps1，中文注释会直接把脚本打花——必须带 UTF-8 BOM；
- 常驻运行必须 --no-sync（HANDOFF：运行时 uv 重建 venv 会失败）；
- 配对码只认 pair_url.json 且必须校验 expires_at（文件可能滞后于服务换码）；
- 服务运行中会让 uv sync 死锁，预检必须存在且 blocked 时不得继续；
- cmd 包装器只允许 ASCII（cmd 代码页随机器变化，中文会乱码甚至执行失败）。

一键图形安装器已于 2026-11-01 废弃（代码在 temp/retired-installer-2026-11-01/），
本文件不再覆盖 GUI 部分；`installer_lib.ps1` 仍被 start.ps1 启动链依赖，守卫保留。
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]

PS1_FILES = (
    "scripts/installer_lib.ps1",
    "scripts/install.ps1",
    "scripts/start.ps1",
)


def _read_script(name: str) -> str:
    # utf-8-sig 兼容带/不带 BOM 两种形态。
    return (ROOT / name).read_text(encoding="utf-8-sig")


def test_installer_scripts_avoid_pwsh7_only_operators():
    """PowerShell 5.1 没有 && / ||；出现即在中文 Windows 上直接语法报错。"""
    for name in PS1_FILES:
        text = _read_script(name)
        assert "&&" not in text, f"{name} 含 PS7 专用的 &&"
        assert "||" not in text, f"{name} 含 PS7 专用的 ||"


def test_installer_scripts_are_utf8_bom():
    """PS 5.1 对无 BOM 的 .ps1 按 ANSI 解析；中文注释/提示必须以 UTF-8 BOM 保存。"""
    for name in PS1_FILES:
        data = (ROOT / name).read_bytes()
        assert data[:3] == b"\xef\xbb\xbf", f"{name} 缺少 UTF-8 BOM"


def test_shared_lib_pins_napcat_hash_and_gates_on_doctor():
    text = _read_script("scripts/installer_lib.ps1")
    # NapCat 版本与官方 sha256 钉死：代理下载路径也具备篡改证据能力
    assert "NapCat.Shell.zip" in text
    assert "6801f93e533846cea05f4f531874a0e84371ab2331c5585b57cd7e3b869cf96b" in text
    assert "Get-FileHash" in text and "SHA256" in text
    # 核心步骤纪律：可复现安装 + 只读 doctor 作为"安装成功"的判定
    assert "uv sync --frozen" in text
    assert "omni.channels.doctor" in text
    # 幂等重装不得覆盖用户密钥
    assert "保留不动" in text


def test_shared_lib_preflights_running_services():
    """服务运行中会让 uv sync 因 .venv 文件锁失败；预检必须先拦（blocked）后指引。"""
    text = _read_script("scripts/installer_lib.ps1")
    assert "Test-LockedEntries" in text
    assert "blocked" in text


def test_workbench_start_follows_no_sync_and_pair_url_contract():
    text = _read_script("scripts/installer_lib.ps1")
    # HANDOFF 纪律：常驻运行必须 --no-sync
    assert "uv run --no-sync omni-web" in text
    # 配对码从 pair_url.json 读取且必须校验 expires_at（滞后文件不能递给用户）
    assert "pair_url.json" in text
    assert "expires_at" in text


def test_bat_wrappers_are_ascii_only_and_start_the_workbench():
    """cmd 代码页随机器变化：包装器只允许 ASCII；start.bat 必须指向 start.ps1（启动链）。

    图形安装器已废弃：install.bat 不再存在，若有人误加回来会被这条断言提醒更新台账。
    """
    assert not (ROOT / "install.bat").exists(), "install.bat 已废弃（temp/retired-installer-2026-11-01/）"
    start_bat = ROOT / "start.bat"
    start_bat.read_bytes().decode("ascii")
    assert "start.ps1" in start_bat.read_text(encoding="ascii")


def test_gitignore_excludes_installer_downloads():
    """tools/ 下是下载的 NapCat / platform-tools，不能进仓库。"""
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "tools/" in text


@pytest.mark.parametrize("case", ["ready", "wrong_boot", "not_ready", "wrong_pair_port"])
def test_pair_link_requires_live_matching_service_identity(tmp_path, case):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            assert self.path == "/api/auth/status?startup=1"
            body = json.dumps(
                {
                    "boot_id": "fixture-boot" if case != "wrong_boot" else "another-boot",
                    "pid": os.getpid(),
                    "ready": case != "not_ready",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port + (1 if case == 'wrong_pair_port' else 0)}/pair/fixture-code"
    target = tmp_path / "data/run/pair_url.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {"url": url, "expires_at": time.time() + 120, "boot_id": "fixture-boot", "pid": os.getpid(), "port": port}
        ),
        encoding="utf-8",
    )
    try:
        command = ". (Join-Path $env:OMNI_TEST_REPOSITORY 'scripts/installer_lib.ps1'); $link = Get-FreshPairUrl -Root $env:OMNI_TEST_PAIR_ROOT; if ($link) { Write-Output $link } else { Write-Output 'NONE' }"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            env={**os.environ, "OMNI_TEST_REPOSITORY": str(ROOT), "OMNI_TEST_PAIR_ROOT": str(tmp_path)},
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        assert result.stdout.strip() == (url if case == "ready" else "NONE"), result.stderr
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)
