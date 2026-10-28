"""只读 doctor 命令；不组装运行态，以免诊断触发建库、迁移或后台任务。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import DATA_DIR, RuntimePaths
from ..system.doctor import diagnose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OmniOperator 只读诊断")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--config")
    parser.add_argument("--skills")
    parser.add_argument("--memory-db", help="记忆库独立路径（与 build_runtime db_path 一致）")
    parser.add_argument("--output", help="可选：显式保存不含对话/密钥的 JSON 报告，拒绝覆盖")
    args = parser.parse_args(argv)
    report = diagnose(RuntimePaths.create(Path(args.data_dir), config_path=args.config, skills_root=args.skills, memory_path=args.memory_db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with Path(args.output).open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text)
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
