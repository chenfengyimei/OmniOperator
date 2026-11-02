"""仓库任务摘要生成入口。"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[1] / "agent/scripts/task_status.py"), run_name="__main__")
