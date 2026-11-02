"""仅做原生ES模块语法检查，不执行界面脚本或访问网络。"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    scripts = [p for p in (ROOT / "web").rglob("*.js") if "node_modules" not in p.parts and "vendor" not in p.parts]
    for path in scripts:
        subprocess.run(["node", "--check", str(path)], check=True)
    print(f"ES modules: {len(scripts)} syntax checks passed")


if __name__ == "__main__":
    main()
