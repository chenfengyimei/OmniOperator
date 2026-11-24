"""生成/检查模块、源码、API 与工具索引；只解析仓库源码，不组装运行时。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from omni.config import PROJECT_ROOT
from omni.system.catalog import build_index
from omni.system.catalog.features import load_features, query, render_features
from omni.system.catalog.render import render_documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path, help="默认 root/docs/v/index")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="不写文件，检查当前索引是否过期")
    action.add_argument("--feature", metavar="名称", help="按功能名、别名或工作台页面查找开发入口")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        features = load_features(root)
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"功能清单校验失败：{exc}")
        return 1
    if args.feature is not None:
        result, matched = query(features, args.feature)
        print(result)
        return 0 if matched else 1
    index = build_index(root)
    if index["warnings"]:
        for warning in index["warnings"]:
            print(warning)
        return 1
    target = args.output or root / "docs/v/index"
    changed = []
    documents = render_documents(index)
    documents["FEATURES.md"] = render_features(features)
    for name, value in documents.items():
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
    # Use UTF-8 for redirected developer output; respect explicitly configured encodings.
    if sys.platform == "win32" and not sys.stdout.isatty() and "PYTHONIOENCODING" not in os.environ:
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
