"""Repository entry for the V2-only Windows offline-candidate evidence gate."""

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "agent/scripts/v2_candidate_gate.py"),
    run_name="__main__",
)
