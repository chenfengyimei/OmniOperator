"""安装器脚本守卫：PS 5.1 兼容、BOM、启动纪律与配对文件契约（静态检查，不执行脚本）。

背景：install_gui.ps1 / install.ps1 / start.ps1 / installer_lib.ps1 是给"不会配置
的用户"用的装机工具，坏了就是装机失败；PowerShell 脚本没有 pytest 之外的测试基建，
这里至少把已知平台坑钉死：
- PowerShell 5.1 不支持 && / ||（本仓库实测过的坑）；
- PS 5.1 按 ANSI 解析无 BOM 的 .ps1，中文注释会直接把脚本打花——必须带 UTF-8 BOM；
- 常驻运行必须 --no-sync（HANDOFF：运行时 uv 重建 venv 会失败）；
- 配对码只认 pair_url.json 且必须校验 expires_at（文件可能滞后于服务换码）；
- 服务运行中会让 uv sync 死锁，预检必须存在且 blocked 时不得继续；
- cmd 包装器只允许 ASCII（cmd 代码页随机器变化，中文会乱码甚至执行失败）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PS1_FILES = (
    "scripts/installer_lib.ps1",
    "scripts/install.ps1",
    "scripts/install_gui.ps1",
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


def test_bat_wrappers_are_ascii_only_and_open_the_gui():
    """cmd 代码页随机器变化：包装器只允许 ASCII；install.bat 必须指向图形安装器。"""
    for name in ("install.bat", "start.bat"):
        (ROOT / name).read_bytes().decode("ascii")
    assert "install_gui.ps1" in (ROOT / "install.bat").read_text(encoding="ascii")


def test_gitignore_excludes_installer_downloads():
    """tools/ 下是下载的 NapCat / platform-tools，不能进仓库。"""
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "tools/" in text
