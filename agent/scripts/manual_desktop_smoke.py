"""显式手动验收：只打开新建的临时记事本文件，不接触已有文档或真实运行数据。"""

import argparse
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import yaml

from omni.bootstrap import build_runtime
from omni.safety.gates import AllowAllApprover


def cleanup_test_tabs(runtime, window_id, names):
    """只关闭明确列出的合成标签页；不按进程名杀共享的 Store 记事本。"""
    outcomes = []
    for name in names:
        observed = runtime.desktop.observe(window_id)
        tabs = [
            node
            for node in observed["state"]["nodes"]
            if node["role"] == "ControlType.TabItem" and node["name"].startswith(name + ".txt")
        ]
        if len(tabs) != 1:
            continue
        tab = tabs[0]
        if not tab.get("selected"):
            selected = runtime.desktop.action(observed["observation"], tab["id"], "select")
            if selected.status != "ok":
                outcomes.append({"tab": name, "closed": False})
                continue
            observed = runtime.desktop.observe(window_id)
        close = next(
            (
                node
                for node in observed["state"]["nodes"]
                if node["parent_id"] == tab["id"] and node["automation_id"] == "CloseButton"
            ),
            None,
        )
        if close is None:
            outcomes.append({"tab": name, "closed": False})
            continue
        runtime.desktop.action(observed["observation"], close["id"], "invoke")
        after = runtime.desktop.observe(window_id)
        outcomes.append(
            {
                "tab": name,
                "closed": not any(
                    node["role"] == "ControlType.TabItem" and node["name"].startswith(name + ".txt")
                    for node in after["state"]["nodes"]
                ),
            }
        )
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="显式允许启动记事本与临时文本编辑")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app", type=Path, default=Path("C:/Windows/System32/notepad.exe"))
    args = parser.parse_args()
    if not args.run:
        parser.error("需要 --run 明确选择此手动真实桌面验收")
    app = args.app.resolve()
    with tempfile.TemporaryDirectory(prefix="omni-notepad-") as directory:
        root = Path(directory)
        title = "OmniOperator-acceptance-" + uuid.uuid4().hex[:8]
        document = root / (title + ".txt")
        original = "OmniOperator temporary desktop acceptance\r\n中文读取测试\r\n"
        document.write_text(original, encoding="utf-8-sig")
        config = root / "config.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "review": {"enabled": False},
                    "desktop": {"enabled": True, "allowed_apps": [str(app)], "max_writes_per_day": 8},
                }
            ),
            encoding="utf-8",
        )
        runtime = build_runtime(config_path=config, data_dir=root / "runtime", skills_root=root / "skills")
        # 用户明确选择的交互验收窗口；所有 UIA 辅助进程仍隐藏。
        process = subprocess.Popen([str(app), str(document)])
        report = {"target": "Windows Notepad", "synthetic_content_only": True, "initial_read": False}
        try:
            window = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                found = runtime.desktop.windows()["windows"]
                window = next((row for row in found if title in row["title"]), None)
                if window:
                    break
                time.sleep(0.2)
            if window is None:
                raise RuntimeError("未发现临时记事本窗口；可能为 Store 版重定向，需显式配置其 exe")
            observed = runtime.desktop.observe(window["hwnd"])
            edits = [node for node in observed["state"]["nodes"] if "中文读取测试" in node.get("value", "")]
            report.update(
                initial_read=bool(edits),
                window_class=window["class"],
                executable=window["exe"],
                dpi=observed["state"]["dpi"],
                nodes=len(observed["state"]["nodes"]),
                supported_edit=any(n["set_value"] for n in edits),
            )
            if not edits or not any(n["set_value"] for n in edits):
                report["limitation"] = "该记事本不提供后台 ValuePattern 编辑；未使用 SendInput 或抢焦点"
            else:
                edit = next(node for node in edits if node["set_value"])
                result = runtime.registry.execute(
                    "desktop_action",
                    {
                        "observation": observed["observation"],
                        "element": edit["id"],
                        "window_title": window["title"],
                        "application": window["exe"],
                        "action": "set_value",
                        "text": "OmniOperator 临时编辑回读成功",
                    },
                    approver=AllowAllApprover(),
                )
                report["edit"] = result.brief()
                report["readback_exact"] = result.status == "ok"
                report["replay_rejected"] = (
                    runtime.desktop.action(observed["observation"], edit["id"], "set_value", "不能重复").status
                    == "error"
                )
                fresh = runtime.desktop.observe(window["hwnd"])
                try:
                    shot = runtime.desktop.screenshot(fresh["observation"])
                    report["screenshot"] = {"png": shot.startswith(b"\x89PNG"), "bytes": len(shot)}
                except Exception as exc:
                    report["screenshot"] = {"error": str(exc)}
            report["cleanup"] = cleanup_test_tabs(runtime, window["hwnd"], [title])
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            # 只终止本脚本 Popen 持有的进程句柄；Store 重定向目标绝不按猜测 PID 杀。
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            runtime.close_retrieval()
            runtime.close_reviewer()
            runtime.close_mcp()
            runtime.close_browser()
            runtime.store.close()
            runtime.close_state()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
