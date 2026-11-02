"""仓库界面语法校验入口。"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[1] / "agent/scripts/check_frontend.py"), run_name="__main__")
