"""V2 dependency bundle build entrypoint; implementation belongs to the Agent product."""

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "agent/scripts/build_v2_dependencies.py"),
    run_name="__main__",
)
