"""源码包安全展开与发行清单；只处理受管源码，不读取用户配置或运行数据。"""

from __future__ import annotations

import ast
import json
import stat
import zipfile
from pathlib import Path

from .common import SHA, UpdateError, digest, managed, no_links, relative_name, write_json
from .version import inventory, product_version


def extract(source: Path, destination: Path, commit: str) -> dict:
    if not SHA.fullmatch(commit) or destination.exists():
        raise UpdateError("invalid_package", "更新包目标已存在或提交无效")
    no_links(destination)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > 12000 or sum(row.file_size for row in entries) > 512 * 1024 * 1024:
            raise UpdateError("invalid_package", "源码归档为空或超过解压上限")
        names = [row.filename.rstrip("/") for row in entries]
        for row in entries:
            relative_name(row.orig_filename.rstrip("/"))
        for name in names:
            relative_name(name)
        prefix = "" if "pyproject.toml" in names else names[0].split("/")[0] + "/"
        if prefix and any(name != prefix[:-1] and not name.startswith(prefix) for name in names):
            raise UpdateError("unsafe_archive", "归档根目录不唯一")
        seen, selected, supplied = set(), [], None
        for row in entries:
            if row.filename.endswith((".py", ".js", ".json", ".toml")) and row.file_size > 16 * 1024 * 1024:
                raise UpdateError("invalid_package", "源码或清单单文件超过上限")
            if stat.S_IFMT(row.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise UpdateError("unsafe_archive", "归档包含链接或特殊文件")
            name = row.filename.rstrip("/")[len(prefix) :]
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                raise UpdateError("unsafe_archive", "归档包含重复或大小写冲突路径")
            seen.add(key)
            if row.is_dir():
                continue
            if name == "omni-build.json":
                if row.file_size > 2 * 1024 * 1024:
                    raise UpdateError("invalid_manifest", "发行清单过大")
                supplied = json.loads(archive.read(row))
            elif managed(name):
                selected.append((row, name))
        destination.mkdir(parents=True)
        for row, name in selected:
            path = no_links(destination / name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(row) as src, path.open("xb") as dst:
                import shutil

                shutil.copyfileobj(src, dst, 65536)
    required = {
        "pyproject.toml",
        "uv.lock",
        "agent/pyproject.toml",
        "agent/src/omni/__init__.py",
        "agent/src/omni/system/updates/worker.py",
    }
    files = inventory(destination)
    if not required <= files.keys():
        raise UpdateError("incompatible_updater", "目标源码缺少兼容的升级协议或锁文件")
    tree = ast.parse((destination / "agent/src/omni/system/updates/worker.py").read_text(encoding="utf-8"))
    if not any(
        isinstance(node, ast.Assign)
        and any(isinstance(name, ast.Name) and name.id == "PROTOCOL_VERSION" for name in node.targets)
        and isinstance(node.value, ast.Constant)
        and node.value.value == 1
        for node in tree.body
    ):
        raise UpdateError("incompatible_updater", "目标源码的升级协议不受当前执行器支持")
    version = product_version(destination)
    receipt = {"schema": 1, "version": version, "commit": commit, "files": files}
    if supplied and supplied != receipt:
        raise UpdateError("invalid_manifest", "发行清单与源码、提交或产品版本不一致")
    write_json(destination / "omni-build.json", receipt)
    return receipt


def verify(root: Path, receipt: dict) -> None:
    if receipt.get("schema") != 1 or not SHA.fullmatch(receipt.get("commit", "")) or not receipt.get("files"):
        raise UpdateError("invalid_manifest", "版本清单无效")
    for name, expected in receipt["files"].items():
        if not managed(name) or not isinstance(expected, str) or len(expected) != 64:
            raise UpdateError("invalid_manifest", "版本清单包含非法文件")
        path = no_links(root / name)
        if not path.is_file() or digest(path) != expected:
            raise UpdateError("source_changed", "源码文件已变化：" + name)
    if product_version(root) != receipt["version"]:
        raise UpdateError("invalid_version", "产品版本与清单不一致")
    if inventory(root) != receipt["files"]:
        raise UpdateError("source_changed", "源码清单包含额外文件或缺少受管文件")
