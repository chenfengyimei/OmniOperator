"""从干净提交生成带版本清单的源码包；不创建标签、不推送、不发布发行版。"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from omni.system.updates.common import UpdateError, git, managed, no_links
from omni.system.updates.version import identify, inventory


def build(root: Path, destination: Path) -> Path:
    current = identify(root)
    if current["installation"] != "git" or current["dirty"]:
        raise UpdateError("local_changes", "发行包只能从干净的官方 Git 提交生成")
    destination = no_links(destination)
    destination.mkdir(parents=True, exist_ok=True)
    names = [name for name in git(root, "ls-files", "-z").split("\0") if name and managed(name)]
    files = inventory(root, names)
    receipt = {"schema": 1, "version": current["version"], "commit": current["commit"], "files": files}
    package = destination / f"omnioperator-{current['version']}-{current['commit'][:12]}.zip"
    with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(no_links(root / name), name)
        archive.writestr("omni-build.json", json.dumps(receipt, ensure_ascii=False, indent=2))
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix(".zip.sha256").write_text(checksum + "  " + package.name + "\n", encoding="utf-8")
    package.with_suffix(".json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.root, args.output))
