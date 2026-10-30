"""显式手动套件：只终止本脚本创建的短寿命 Python 父子进程，不访问常驻服务。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from omni.system.process_identity import confirmed_exited, inspect_process, terminate_verified_tree


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.run or os.name != "nt":
        parser.error("需 Windows 并显式指定 --run")
    code = ("import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
            "creationflags=0x08000000,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            "print(p.pid,flush=True); time.sleep(60)")
    root = subprocess.Popen([sys.executable, "-c", code], creationflags=0x08000000,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    report = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "platform": "Windows",
              "scope": "script-owned temporary Python parent and child; no resident service"}
    child_identity = None
    try:
        child_pid = int(root.stdout.readline().strip())
        root_identity = inspect_process(root.pid)
        child_identity = inspect_process(child_pid)
        assert root_identity and child_identity
        assert int(child_identity.created) >= int(root_identity.created)
        report["identities_captured"] = True
        terminate_verified_tree(root_identity)
        report["parent_exit_confirmed"] = confirmed_exited(root_identity)
        report["child_exit_confirmed"] = confirmed_exited(child_identity)
        assert report["parent_exit_confirmed"] and report["child_exit_confirmed"]
        root.wait(timeout=5)
        report["passed"] = True
    finally:
        if root.poll() is None:
            root.terminate()  # Popen 保留的本次创建句柄，不能被 PID 重用替换。
            root.wait(timeout=5)
        if child_identity and not confirmed_exited(child_identity):
            terminate_verified_tree(child_identity)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
