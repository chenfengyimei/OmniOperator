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


def test_files(root: Path) -> list[str]:
    """Discover the nested suite explicitly; an empty suite is a broken gate."""
    tests = sorted((root / "agent/tests/v2").rglob("test_v2_*.py"))
    if not tests:
        raise ValueError("V2 test suite is empty")
    return [str(path.relative_to(root)) for path in tests]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "temp/build/v2-ci")
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    tests = test_files(ROOT)
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
