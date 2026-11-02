"""只读 doctor 命令；不组装运行态，以免诊断触发建库、迁移或后台任务。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..system.agent_spaces.inspection import load_runtime_paths
from ..system.doctor import diagnose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OmniOperator 只读诊断")
    parser.add_argument("--data-dir", help="控制数据根；省略时使用启动入口的环境配置")
    parser.add_argument("--config")
    parser.add_argument("--skills")
    parser.add_argument("--memory-db", help="记忆库独立路径（与 build_runtime db_path 一致）")
    parser.add_argument("--agent", help="指定 Agent；省略时使用环境或注册表默认选择")
    parser.add_argument("--directory-catalog", help="目录注册表路径")
    parser.add_argument("--output", help="可选：显式保存不含对话/密钥的 JSON 报告，拒绝覆盖")
    args = parser.parse_args(argv)
    try:
        paths = load_runtime_paths(
            data_dir=args.data_dir,
            config_path=args.config,
            skills_root=args.skills,
            memory_path=args.memory_db,
            agent_id=args.agent,
            directory_catalog=args.directory_catalog,
        )
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "read_only": True,
                    "error": "目录配置无法解析，未检查其它数据目录",
                    "code": type(exc).__name__,
                },
                ensure_ascii=False,
            )
        )
        return 2
    report = diagnose(paths)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with Path(args.output).open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text)
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
