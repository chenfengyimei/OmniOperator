"""Repository entry for the standalone offline Windows V2 installer."""

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "agent/scripts/install_v2.py"),
    run_name="__main__",
)
