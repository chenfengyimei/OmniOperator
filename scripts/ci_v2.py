"""Offline validation for portable V2 and the Windows repository."""

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
WINDOWS_UIA_TESTS = (
    "agent/tests/v2/platforms/common/test_v2_desktop_observation_native.py",
    "agent/tests/v2/platforms/common/test_v2_foreground_worker.py",
)


def test_files(root: Path) -> list[str]:
    """Discover the nested suite explicitly; an empty suite is a broken gate."""
    tests = sorted((root / "agent/tests/v2").rglob("test_v2_*.py"))
    if not tests:
        raise ValueError("V2 test suite is empty")
    return [str(path.relative_to(root)) for path in tests]


def windows_repository_tests(evidence: Path, part: str) -> int:
    pytest = [sys.executable, "-m", "pytest", "-q", "-c", str(ROOT / "pyproject.toml")]
    status = 0
    if part in {"all", "native"}:
        # Run native compilation and owned desktop windows without other test workers.
        native = subprocess.run(
            [
                *pytest,
                "-n",
                "0",
                *WINDOWS_UIA_TESTS,
                f"--junitxml={evidence / 'pytest-windows-native.xml'}",
            ],
            cwd=ROOT,
            check=False,
        )
        status = native.returncode
    if part in {"all", "remaining"}:
        remaining = subprocess.run(
            [
                *pytest,
                *(f"--ignore={path}" for path in WINDOWS_UIA_TESTS),
                f"--junitxml={evidence / 'pytest.xml'}",
            ],
            cwd=ROOT,
            check=False,
        )
        status = status or remaining.returncode
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "temp/build/v2-ci")
    parser.add_argument(
        "--windows-repository",
        nargs="?",
        const="all",
        choices=("all", "native", "remaining"),
        help="Run the Windows repository suite or one of its two parts",
    )
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    if args.windows_repository:
        return windows_repository_tests(args.evidence, args.windows_repository)
    tests = [*test_files(ROOT), "agent/tests/test_filesystem_guard.py"]
    commands = [
        [sys.executable, "-m", "ruff", "check", *SOURCES, *tests],
        [sys.executable, "-m", "ruff", "format", "--check", *SOURCES, *tests],
        [sys.executable, "-m", "pyright", *SOURCES],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
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
