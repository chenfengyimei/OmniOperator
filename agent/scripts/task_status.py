"""从唯一任务清单生成当前摘要；--check用于CI，不改历史会话记录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATES = {"待开始", "开发中", "待真实验收", "完成", "阻塞"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    selected = "v2.json" if (ROOT / "docs/tasks/v2.json").is_file() else "unification.json"
    tasks = json.loads((ROOT / "docs/tasks" / selected).read_text(encoding="utf-8"))
    lines = [
        "# 当前开发状态",
        "",
        f"> 本文由 docs/tasks/{selected} 生成；历史证据不改写为当前测试结论。",
        "",
        "| 批次 | 内容 | 状态 | 进展与剩余项 |",
        "|---|---|---|---|",
    ]
    ids = set()
    for item in tasks["tasks"]:
        assert item["status"] in STATES and item["id"] not in ids
        ids.add(item["id"])
        assert item["status"] != "完成" or item.get("evidence"), "完成必须关联验证证据"
        item = {**item, "progress": item.get("progress", item.get("notes", ""))}
        lines.append(
            "| "
            + " | ".join(
                str(item[k]).replace("|", "\\|").replace("\n", " ") for k in ("id", "title", "status", "progress")
            )
            + " |"
        )
    packages = tasks.get("delivery_packages", [])
    if packages:
        lines.extend(
            [
                "",
                "## 当前交付清单",
                "",
                "开发、离线验证与平台实测分别记录；历史子批次不等于完整交付。",
                "",
                "| 交付包 | 内容 | 开发 | 离线验证 | 平台实测 | 下一退出条件 |",
                "|---|---|---|---|---|---|",
            ]
        )
        for item in packages:
            assert item["development"] in STATES and item["offline_validation"] in STATES
            values = [
                item["id"],
                item["title"],
                item["development"],
                item["offline_validation"],
                item["platform_acceptance"],
                item["exit_criteria"],
            ]
            lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |")
    lines.extend(
        [
            "",
            "历史台账：[整改前完整记录](../temp/archive/docs/2026-11-01/DEVELOPMENT_STATUS.md)。",
            "所有14份会话来源及旧编号映射见 [任务清单](tasks/unification.json)；规则见 [UNIFICATION.md](UNIFICATION.md)。",
            "真实验收步骤和授权目标见 [验收手册](UNIFICATION_ACCEPTANCE.md)。",
            "V2当前实施边界与剩余项见 [重构记录](V2_REFACTOR.md) 和 [任务台账](tasks/v2.json)。",
            "",
        ]
    )
    output = "\n".join(lines)
    target = ROOT / "docs/DEVELOPMENT_STATUS.md"
    if args.check:
        if target.read_text(encoding="utf-8") != output:
            print("当前摘要已过期：运行 python scripts/task_status.py")
            return 1
        return 0
    target.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
