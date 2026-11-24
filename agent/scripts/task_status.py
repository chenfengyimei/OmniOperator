"""从唯一任务清单生成当前摘要；--check用于CI，不改历史会话记录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def render_current(development: dict, windows: dict) -> str:
    """分别展示当前任务与固定 Windows 候选的验收结果。"""
    labels = {
        "implemented_scoped_passed": "已实现／专项通过",
        "development_in_progress": "开发中",
        "waiting_for_external_acceptance": "待外部验收",
        "accepted": "验收完成",
        "deferred_by_scope": "本版延期",
        "code_audit_in_progress": "代码核对中",
        "waiting_for_development_completion": "等待开发完成",
    }
    lines = [
        "# 当前开发状态",
        "",
        "> 从 [开发台账](tasks.json) 生成。更新台账后运行 `uv run --no-sync python scripts/task_status.py`。",
        "",
        development["summary"],
        "",
        "| 任务 | 内容 | 当前状态 | 下一步或验收条件 | 依据 |",
        "|---|---|---|---|---|",
    ]
    ids = set()
    for item in development["requirements"]:
        identity, status = item["id"], item["status"]
        if identity in ids or status not in labels:
            raise ValueError("Duplicate task or unknown current status")
        ids.add(identity)
        if status in {"implemented_scoped_passed", "accepted"} and not item.get("evidence"):
            raise ValueError("Completion requires evidence")
        references = ([item["contract"]] if item.get("contract") else []) + item.get("evidence", [])
        links = "、".join(f"[{Path(name).stem}]({name.removeprefix('docs/v/')})" for name in references)
        values = [identity, item["title"], labels[status], item.get("next", ""), links or "—"]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |")
    if windows["status"] != "completed_windows_offline_candidate":
        raise ValueError("Windows stage needs explicit reclassification")
    lines.extend(
        [
            "",
            "## Windows 阶段与后续验收",
            "",
            windows["summary"],
            "",
            "开发分工和接续步骤见 [交接](HANDOFF.md)。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    directory = ROOT / "docs/v"
    ledger = json.loads((directory / "tasks.json").read_text(encoding="utf-8"))
    output = render_current(ledger, ledger["windows_stage"])
    target = directory / "STATUS.md"
    if args.check:
        return 0 if target.is_file() and target.read_text(encoding="utf-8") == output else 1
    target.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
