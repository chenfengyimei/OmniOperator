"""固定Pyright版本逐域检查；当前覆盖预设、归档、凭据和远程MCP。

用法：uv run --no-sync python scripts/run_pyright.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "temp" / "build" / "pyright.json"


def main() -> int:
    proc = subprocess.run(
        [
            "uvx",
            "--from",
            "pyright==1.1.414",
            "pyright",
            "--pythonpath",
            str(ROOT.parent / ".venv" / "Scripts" / "python.exe"),
            "--outputjson",
            "src/omni/system/persona_presets.py",
            "src/omni/system/archive.py",
            "src/omni/system/protected_json.py",
            "src/omni/tools/mcp/remote.py",
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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(lines[0])
    print(f"error 明细已写入 {OUT}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
