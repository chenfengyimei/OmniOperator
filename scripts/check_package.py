"""仓库打包验证入口；实现归Agent产品。"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[1] / "agent/scripts/check_package.py"), run_name="__main__")
