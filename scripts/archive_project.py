"""仓库归档命令兼容入口，实际实现归agent/scripts。"""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'agent/scripts/archive_project.py'), run_name='__main__')
