"""兼容旧开发命令；正式实现位于 agent/scripts/manual_desktop_smoke.py。"""
from pathlib import Path
import runpy

globals().update(runpy.run_path(str(Path(__file__).resolve().parents[1]/'agent/scripts/manual_desktop_smoke.py'),run_name=__name__))
