"""内置终端单测：真开 ConPTY 会话做回显冒烟（仅 Windows）+ 会话池限额。"""
import sys

import pytest

from omni.channels.terminal import TerminalManager, TerminalSession

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ConPTY 仅 Windows")


def _read_until(session, needle: str, max_chunks: int = 400) -> str:
    """反复读直到出现目标串（ConPTY 的 VT 序列是碎块，预算要给足）。"""
    got = ""
    for _ in range(max_chunks):
        got += session.read()
        if needle in got:
            break
    return got


def test_terminal_session_echo_roundtrip():
    session = TerminalSession(cols=100, rows=25)
    try:
        banner = _read_until(session, ">")  # PowerShell 提示符
        assert banner, "没有收到 PowerShell 启动输出"
        session.write("echo omni-term-ok\r\n")
        out = _read_until(session, "omni-term-ok")
        assert "omni-term-ok" in out
    finally:
        session.kill()
    assert not session.isalive()


def test_terminal_session_resize_and_chinese():
    session = TerminalSession(cols=100, rows=25)
    try:
        _read_until(session, ">")
        session.resize(120, 30)  # 不抛即通过
        session.write("echo 你好小鱼\r\n")
        out = _read_until(session, "小鱼")
        assert "小鱼" in out  # ConPTY 输出应正确解码中文
    finally:
        session.kill()


def test_manager_cap_and_release():
    manager = TerminalManager(max_sessions=1)
    first = manager.open()
    with pytest.raises(RuntimeError, match="上限"):
        manager.open()
    manager.close(first)
    second = manager.open()  # 归还后可再开
    manager.close(second)
