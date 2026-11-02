"""静态 UIAutomation 辅助程序。用户参数只经 stdin JSON 传递，不拼接 PowerShell。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class DesktopError(RuntimeError):
    def __init__(self, message: str, *, unknown: bool = False):
        super().__init__(message)
        self.unknown = unknown


class WindowsDesktopBackend:
    def __init__(self, *, timeout_s: float = 15, max_nodes: int = 100):
        self.timeout_s, self.max_nodes = timeout_s, max_nodes

    def request(self, command: str, **arguments) -> dict:
        if os.name != "nt":
            raise DesktopError("桌面适配仅支持 Windows")
        helper = Path(__file__).with_name("uia.ps1")
        shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        try:
            result = subprocess.run(
                [str(shell), "-NoProfile", "-NonInteractive", "-File", str(helper)],
                input=json.dumps({"command": command, "max_nodes": self.max_nodes, **arguments}, ensure_ascii=False),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout_s,
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise DesktopError(
                "桌面调用超时或辅助程序不可用", unknown=command in {"action", "foreground_action"}
            ) from None
        if len(result.stdout) > 8_000_000:
            raise DesktopError("桌面响应超限", unknown=command in {"action", "foreground_action"})
        try:
            payload = json.loads(result.stdout.lstrip("\ufeff"))
        except (ValueError, TypeError):
            raise DesktopError("无法解析桌面响应", unknown=command in {"action", "foreground_action"}) from None
        if not payload.get("ok"):
            raise DesktopError(str(payload.get("error") or "桌面操作失败"), unknown=bool(payload.get("unknown")))
        return payload
