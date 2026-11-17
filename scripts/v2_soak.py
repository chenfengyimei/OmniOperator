"""Repository entrypoint for V2-only read-only soak evidence."""

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "agent/scripts/v2_soak.py"),
    run_name="__main__",
)
