"""从唯一任务清单生成当前摘要；--check用于CI，不改历史会话记录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATES = {"待开始", "开发中", "待真实验收", "完成", "阻塞"}


def render_current(development: dict, windows: dict) -> str:
    """当前 G 任务和固定 Windows 阶段分别展示，不从旧 R/D 台账推导状态。"""
    labels = {
        "implemented_scoped_passed": "已实现／专项通过",
        "development_in_progress": "开发中",
        "code_audit_in_progress": "代码核对中",
        "waiting_for_development_completion": "等待开发完成",
    }
    lines = [
        "# 当前开发状态",
        "",
        "> 由 docs/tasks/v2-development.json 与 docs/tasks/v2-windows-candidate.json 生成；历史证据只属于其固定源码。",
        "",
        "整个 V2 仍在开发，当前源码未统一全量。默认入口为 V2，显式 V1 回退保留。",
        "",
        "| 任务 | 内容 | 当前状态 | 合同与证据 |",
        "|---|---|---|---|",
    ]
    ids = set()
    for item in development["requirements"]:
        identity, status = item["id"], item["status"]
        if identity in ids or status not in labels:
            raise ValueError("Duplicate task or unknown current status")
        ids.add(identity)
        if status == "implemented_scoped_passed" and not item.get("evidence"):
            raise ValueError("Scoped completion requires evidence")
        references = ([item["contract"]] if item.get("contract") else []) + item.get("evidence", [])
        links = "、".join(f"[{Path(name).stem}]({name.removeprefix('docs/')})" for name in references)
        values = [identity, item["title"], labels[status], links or "见持续开发任务"]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |")
    if windows["status"] != "completed_windows_offline_candidate":
        raise ValueError("Windows stage needs explicit reclassification")
    lines.extend(
        [
            "",
            "## Windows 阶段与后续验收",
            "",
            "Windows 主体开发与旧离线候选完成。历史 5998 全量只覆盖 7e64d348；不代表目录迁移后的当前源码。",
            "",
            "自有窗口前台复验已通过，生产 unknown 回执保留。真实账号、模型质量、设备、干净机器及 72 小时仍待验。",
            "",
            "Windows 与 Mac mini 共同迭代 `v2`。分工、同步和集成流程见 [双设备协作开发规范](V2_MULTI_DEVICE_DEVELOPMENT.md)。",
            "",
            "当前真值：[持续开发任务](tasks/v2-development.json)、[Windows 阶段](tasks/v2-windows-candidate.json)。",
            "",
            "历史交付范围：[旧 R/D 台账](tasks/v2.json)。目录与接手：[HANDOFF](HANDOFF.md)、[V2 导航](v2/README.md)。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = ROOT / "docs/tasks/v2-development.json"
    if current.is_file():
        output = render_current(
            json.loads(current.read_text(encoding="utf-8")),
            json.loads((ROOT / "docs/tasks/v2-windows-candidate.json").read_text(encoding="utf-8")),
        )
        target = ROOT / "docs/DEVELOPMENT_STATUS.md"
        if args.check:
            return 0 if target.read_text(encoding="utf-8") == output else 1
        target.write_text(output, encoding="utf-8")
        return 0
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
