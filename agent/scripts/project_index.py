"""生成/检查模块、源码、API 与工具索引；只解析仓库源码，不组装运行时。"""

from __future__ import annotations

import argparse
from pathlib import Path

from omni.config import PROJECT_ROOT
from omni.system.catalog import build_index
from omni.system.catalog.render import render_documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path, help="默认 root/docs/index")
    parser.add_argument("--check", action="store_true", help="不写文件，检查当前索引是否过期")
    args = parser.parse_args()
    root = args.root.resolve()
    index = build_index(root)
    if index["warnings"]:
        for warning in index["warnings"]:
            print(warning)
        return 1
    target = args.output or root / "docs/index"
    changed = []
    for name, value in render_documents(index).items():
        path = target / name
        if not path.is_file() or path.read_text(encoding="utf-8") != value:
            changed.append(name)
            if not args.check:
                target.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding="utf-8")
    print(
        f"modules={len(index['modules'])} files={len(index['files'])} routes={len(index['endpoints'])} declared_tools={len(index['declared_tools'])}"
    )
    print(("stale=" if args.check else "updated=") + ",".join(changed))
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
