"""兼容旧开发命令；正式实现位于 agent/scripts/run_pyright.py。"""
from pathlib import Path
import runpy

globals().update(runpy.run_path(str(Path(__file__).resolve().parents[1]/'agent/scripts/run_pyright.py'),run_name=__name__))
