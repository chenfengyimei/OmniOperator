"""验证直接wheel与sdist→wheel的界面字节一致，并在隔离目录导入安装资源。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from omni.adapters.build_identity import CODE_CACHE_DIRS, CODE_SUFFIXES

ROOT = Path(__file__).resolve().parents[2]
SUFFIXES = {".html", ".js", ".css", ".svg", ".woff", ".woff2", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "temp/build/packaging")
    args = parser.parse_args()
    folder = args.build_dir.resolve()
    for kind, options in (("sdist", []), ("direct", ["--wheel"])):
        subprocess.run(
            ["uv", "build", "--offline", "--package", "omnioperator", *options, "--out-dir", str(folder / kind)],
            cwd=ROOT,
            check=True,
        )
    expected = {
        p.relative_to(ROOT / "web").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "web").rglob("*")
        if p.is_file() and "node_modules" not in p.parts and p.suffix in SUFFIXES
    }
    assert "index.html" in expected and "ui/app/main.js" in expected
    kernel = {
        "omni/" + p.relative_to(ROOT / "agent/src/omni").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for layer in ("domain", "application", "adapters", "composition")
        for p in (ROOT / "agent/src/omni" / layer).rglob("*")
        if p.is_file() and p.suffix in CODE_SUFFIXES and not CODE_CACHE_DIRS.intersection(p.parts)
    }
    for wheel in [*folder.glob("sdist/*.whl"), *folder.glob("direct/*.whl")]:
        with zipfile.ZipFile(wheel) as archive:
            assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in kernel.items()), (
                "V2 kernel changed during packaging"
            )
            actual = {
                n.removeprefix("omni/web_assets/"): hashlib.sha256(archive.read(n)).hexdigest()
                for n in archive.namelist()
                if n.startswith("omni/web_assets/") and not n.endswith("/")
            }
            assert actual == expected, f"wheel资源不一致: {wheel}"
            assert not any("node_modules" in n or "package-lock.json" in n for n in archive.namelist())
            with tempfile.TemporaryDirectory(prefix="omni-wheel-") as temporary:
                archive.extractall(temporary)
                code = "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from omni.config import web_root; from omni.channels.web import _STATIC_DIR; expected=Path(sys.argv[1])/'omni/web_assets'; assert web_root()==expected==_STATIC_DIR; assert (expected/'ui/app/main.js').is_file()"
                subprocess.run([sys.executable, "-I", "-c", code, temporary], cwd=temporary, check=True)
                v2_check = """
import asyncio, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from omni.composition.instance import open_instance
from omni.composition.http import web_assets
async def check():
    expected = Path(sys.argv[1]) / 'omni/web_assets'
    assert web_assets() == expected
    assert (expected / 'ui/v2/index.html').is_file()
    instance = await open_instance(Path(sys.argv[1]) / 'isolated-v2-home')
    try:
        assert instance.health()['ready']
        assert len(await instance.application.query('agents', '')) == 1
        assert 'omni.bootstrap' not in sys.modules
        assert not any(name.startswith('omni.memory.') for name in sys.modules)
    finally:
        assert (await instance.close())['complete']
asyncio.run(check())
"""
                subprocess.run([sys.executable, "-I", "-c", v2_check, temporary], cwd=temporary, check=True)
    with tarfile.open(next(folder.glob("sdist/*.tar.gz"))) as archive:
        actual = {
            n.name.split("/_web/", 1)[1]: hashlib.sha256(archive.extractfile(n).read()).hexdigest()
            for n in archive.getmembers()
            if n.isfile() and "/_web/" in n.name
        }
        assert actual == expected, "sdist资源不一致"
    report = {
        "assets": len(expected),
        "sha256": hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest(),
        "chains": ["direct wheel", "sdist to wheel", "isolated import"],
        "v2": ["packaged assets", "isolated fresh schema", "no V1 imports", "clean shutdown"],
        "v2_kernel_sha256": hashlib.sha256(json.dumps(kernel, sort_keys=True).encode()).hexdigest(),
    }
    (folder / "evidence.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
