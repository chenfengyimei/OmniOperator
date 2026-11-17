"""V2 fresh-install verification entrypoint; implementation belongs to the Agent product."""

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "agent/scripts/check_v2_installation.py"),
    run_name="__main__",
)
