"""源码身份与 SemVer 比较；不联网、不改变 Git 或安装环境。"""

from __future__ import annotations

import re
from pathlib import Path

from .common import REPOSITORY, UpdateError, digest, git, managed, no_links, read_json

OFFICIAL_ORIGINS = {
    REPOSITORY,
    "git@gitee.com:chenfengloveyuri/OmniOperator",
    "ssh://git@gitee.com/chenfengloveyuri/OmniOperator",
    "ssh://git@gitee.com:22/chenfengloveyuri/OmniOperator",
}

SEMVER = re.compile(r"^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")


def semver(value: str) -> tuple:
    match = SEMVER.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise UpdateError("invalid_version", "版本号不符合 SemVer")
    if match[5] and any(not part for part in match[5].split(".")):
        raise UpdateError("invalid_version", "构建标识不能为空")
    parts = match.group(4)
    pre = []
    if parts is not None:
        for part in parts.split("."):
            if not part or (part.isdigit() and len(part) > 1 and part.startswith("0")):
                raise UpdateError("invalid_version", "无效的预发行版本号")
            pre.append((0, int(part)) if part.isdigit() else (1, part))
    return (*(int(match.group(i)) for i in (1, 2, 3)), parts is None, tuple(pre))


def product_version(root: Path) -> str:
    text = (root / "agent/src/omni/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text, re.M)
    if not match:
        raise UpdateError("invalid_version", "找不到产品版本声明")
    semver(match[1])
    return match[1]


def inventory(root: Path, names: list[str] | None = None) -> dict[str, str]:
    if names is None:
        names = []
        from .common import MANAGED_ROOTS

        for base in sorted(MANAGED_ROOTS):
            folder = root / base
            if folder.exists():
                for path in folder.rglob("*"):
                    name = path.relative_to(root).as_posix()
                    if managed(name) and path.is_file():
                        names.append(name)
        from .common import MANAGED_FILES

        names.extend(name for name in MANAGED_FILES if (root / name).is_file())
    result = {}
    for name in sorted(set(names)):
        if managed(name):
            path = no_links(root / name)
            if path.is_file():
                result[name] = digest(path)
    return result


def identify(root: Path) -> dict:
    root = no_links(root)
    receipt = read_json(root / "omni-build.json", {})
    if not isinstance(receipt, dict):
        raise UpdateError("invalid_manifest", "安装版本清单格式无效")
    commit, dirty, installation = receipt.get("commit", ""), [], "archive" if receipt else "unknown"
    if (root / ".git").exists():
        installation = "git"
        commit = git(root, "rev-parse", "HEAD")
        dirty = git(root, "status", "--porcelain=v1", "--untracked-files=normal").splitlines()
        remote = git(root, "remote", "get-url", "origin").rstrip("/").removesuffix(".git")
        if remote not in OFFICIAL_ORIGINS:
            raise UpdateError("untrusted_repository", "origin 不是当前项目的官方 Gitee 仓库")
    elif receipt:
        baseline = receipt.get("files", {})
        if not isinstance(baseline, dict) or not baseline or any(not managed(name) for name in baseline):
            raise UpdateError("invalid_manifest", "安装版本清单无效")
        dirty = [
            name
            for name, expected in baseline.items()
            if not (root / name).is_file() or digest(no_links(root / name)) != expected
        ]
        dirty.extend(sorted(inventory(root).keys() - baseline.keys()))
    files = inventory(root)
    import hashlib
    import json

    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {
        "version": product_version(root),
        "commit": commit,
        "installation": installation,
        "dirty": dirty[:100],
        "fingerprint": fingerprint,
        "repository": REPOSITORY,
    }
