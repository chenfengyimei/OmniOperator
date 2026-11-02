"""Cross-platform V2 offline gate; uses the existing environment without synchronizing it."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    f"agent/src/omni/{name}"
    for name in ("domain", "application", "adapters", "composition")
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "temp/build/v2-ci")
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    tests = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "agent/tests").glob("test_v2_*.py"))
    ]
    commands = [
        [sys.executable, "-m", "ruff", "check", *SOURCES, *tests],
        [sys.executable, "-m", "ruff", "format", "--check", *SOURCES, *tests],
        [sys.executable, "-m", "pyright", *SOURCES],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "0",
            *tests,
            "--junitxml",
            str(args.evidence / "pytest.xml"),
        ],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
