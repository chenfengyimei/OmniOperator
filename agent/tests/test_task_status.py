"""Current task summaries must not promote historical evidence into a new acceptance."""

import runpy
from pathlib import Path

import pytest

render = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/task_status.py"))["render_current"]


def test_current_summary_uses_current_tasks_and_keeps_windows_acceptance_separate():
    output = render(
        {"requirements": [{"id": "G6", "title": "Current work", "status": "development_in_progress"}]},
        {"status": "completed_windows_offline_candidate", "tasks": [{"id": "R1", "status": "完成"}]},
    )
    assert "G6" in output and "Current work" in output and "开发中" in output
    assert "| R1 |" not in output
    assert "当前源码未统一全量" in output and "72 小时仍待验" in output
    assert "7e64d348" in output


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": "G1", "title": "x", "status": "completed"}],
        [{"id": "G1", "title": "x", "status": "implemented_scoped_passed"}],
        [{"id": "G1", "title": "x", "status": "development_in_progress"}] * 2,
    ],
)
def test_current_summary_rejects_unproven_or_ambiguous_completion(rows):
    with pytest.raises(ValueError):
        render({"requirements": rows}, {"status": "completed_windows_offline_candidate"})
