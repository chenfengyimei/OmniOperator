"""shell 工具单测：只读白名单 / 输出格式化 / 超时 / 编码容错（runner 注入离线跑）。"""
import subprocess

import pytest

from omni.tools.registry import ToolRegistry
from omni.tools.shell_tools import decode_output, register_shell_tools


def _registry(runner):
    registry = ToolRegistry()
    register_shell_tools(registry, runner=runner)
    return registry


def test_decode_output_prefers_utf8_then_gbk():
    assert decode_output("中文".encode("utf-8")) == "中文"
    assert decode_output("中文".encode("gbk")) == "中文"
    # 两种编码都解不动 → 容错替换不抛异常
    assert "\ufffd" in decode_output(b"\xff\xfe\xfa")


def test_peek_whitelist_allows_readonly(tmp_path):
    calls: list[str] = []

    def runner(cmd: str, timeout_s: float):
        calls.append(cmd)
        return 0, b"EMU1\tdevice", b""

    registry = _registry(runner)
    out = registry.dispatch("peek_command", {"command": "adb devices"})
    assert "退出码 0" in out and "EMU1" in out
    out = registry.dispatch("peek_command", {"command": "git status"})
    out = registry.dispatch("peek_command", {"command": "tasklist /FI \"PID eq 1\""})
    assert len(calls) == 3


def test_peek_blocks_mutating_commands(tmp_path):
    registry = _registry(lambda cmd, t: (0, b"", b""))
    for evil in ("del foo.txt", "git push", "rm -rf /", "uv add requests"):
        out = registry.dispatch("peek_command", {"command": evil})
        assert "只读白名单" in out


def test_run_command_formats_output(tmp_path):
    def runner(cmd: str, timeout_s: float):
        return 3, b"line1\nline2", b"warn"

    registry = _registry(runner)
    out = registry.dispatch("run_command", {"command": "whatever"})
    assert "退出码 3" in out
    assert "[输出]\nline1\nline2" in out
    assert "[错误输出]\nwarn" in out


def test_run_command_timeout_and_empty(tmp_path):
    def timeout_runner(cmd: str, timeout_s: float):
        raise subprocess.TimeoutExpired(cmd, timeout_s)

    registry = _registry(timeout_runner)
    out = registry.dispatch("run_command", {"command": "ping 127.0.0.1 -n 999"})
    assert "超时" in out

    registry2 = _registry(lambda cmd, t: (0, b"", b""))
    out = registry2.dispatch("run_command", {"command": "cls"})
    assert "无输出" in out


def test_run_command_clips_long_output(tmp_path):
    def runner(cmd: str, timeout_s: float):
        return 0, ("x" * 20000).encode(), b""

    registry = _registry(runner)
    out = registry.dispatch("run_command", {"command": "type big.log"})
    assert len(out) < 9000 and "仅保留末尾" in out


def test_risk_levels(tmp_path):
    registry = _registry(lambda cmd, t: (0, b"", b""))
    assert registry.get("peek_command").risk.value == "safe"
    assert registry.get("run_command").risk.value == "confirm"
