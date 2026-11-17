"""Packaging truth guard: the V2 build gate exists and the packaging document stays honest."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "agent/scripts/check_package.py"
DOC = ROOT / "docs/V2_PACKAGING.md"


def test_build_gate_keeps_its_v2_checks():
    source = CHECK.read_text(encoding="utf-8")
    for marker in (
        "open_instance",
        "omni/web_assets",
        "isolated-v2-home",
        "'omni.bootstrap' not in sys.modules",
        "node_modules",
        "v2_kernel_sha256",
        "_web/",
    ):
        assert marker in source, f"check_package.py lost V2 check: {marker}"


def test_packaging_document_lists_verified_chain_and_unfinished_platforms():
    text = DOC.read_text(encoding="utf-8")
    assert "uv run --no-sync python scripts/check_package.py" in text
    assert "默认入口为 V2" in text
    for case in (
        "test_v2_releases.py",
        "test_v2_release_drain.py",
        "test_v2_deployment_files.py",
        "test_v2_schema_release.py",
    ):
        assert case in text
    for gap in ("macOS/Linux", "容器镜像", "Windows候选独立安装", "代码签名", "干净机器"):
        assert gap in text, f"packaging document hides gap: {gap}"
    assert "不得写“三平台已交付”" in text
    assert "| 三平台已交付 |" not in text
    assert "全平台通过" not in text
    assert (ROOT / "scripts/install.ps1").is_file()
