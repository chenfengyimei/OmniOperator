"""Run the APK's pure editor core with a local JDK; never use ADB or require Android hardware."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "src/omni/adapters/platform/android_input_helper/src/org/omnioperator/inputhelper/SessionCore.java"
    checks = root / "tests/native/android_input/SessionCoreChecks.java"
    suffix = ".exe" if os.name == "nt" else ""
    with tempfile.TemporaryDirectory(prefix="omni-input-core-") as temporary:
        subprocess.run(
            [
                str(args.java_home / ("bin/javac" + suffix)),
                "--release",
                "11",
                "-encoding",
                "UTF-8",
                "-d",
                temporary,
                str(source),
                str(checks),
            ],
            check=True,
            timeout=60,
        )
        subprocess.run(
            [
                str(args.java_home / ("bin/java" + suffix)),
                "-cp",
                temporary,
                "org.omnioperator.inputhelper.SessionCoreChecks",
            ],
            check=True,
            timeout=30,
        )


if __name__ == "__main__":
    main()
