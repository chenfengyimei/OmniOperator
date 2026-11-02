"""显式选择文件的plan/apply/restore归档入口；默认不扫描或改动用户数据。"""

from __future__ import annotations

import argparse
from pathlib import Path

from omni.config import PROJECT_ROOT
from omni.system.archive import execute, plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "restore"))
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--category", choices=("archive", "private", "build"), default="private")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    if args.action == "plan":
        print(plan(args.root, args.paths, category=args.category, reason=args.reason))
    else:
        if len(args.paths) != 1:
            parser.error("apply/restore只能指定一个归档清单")
        state = execute(Path(args.paths[0]), restore=args.action == "restore")
        print(f"entries={len(state['entries'])} action={args.action}")


if __name__ == "__main__":
    main()
