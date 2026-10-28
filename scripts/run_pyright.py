"""一次性：用项目 venv 跑 pyright，把 error 级发现写成 UTF-8 文本。

用法：uv run --no-sync python scripts/run_pyright.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scripts" / "_pyright_findings.txt"


def main() -> int:
    proc = subprocess.run(
        [
            "uvx", "pyright",
            "--pythonpath", str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "--outputjson",
            "src/omni",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    data = json.loads(proc.stdout)
    summary = data["summary"]
    errors = [d for d in data["generalDiagnostics"] if d["severity"] == "error"]
    lines = [
        f"summary: {summary['errorCount']} errors / {summary['warningCount']} warnings / "
        f"{summary['informationCount']} infos"
    ]
    for item in errors:
        rel = item["file"].replace("\\", "/").split("/src/")[-1]
        line = item["range"]["start"]["line"] + 1
        rule = item.get("rule") or "-"
        message = item["message"].replace("\n", " ")[:200]
        lines.append(f"E {rel}:{line} {rule} {message}")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(lines[0])
    print(f"error 明细已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
