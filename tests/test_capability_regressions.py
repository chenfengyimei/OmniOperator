"""能力完善首批回归：只使用假设备、假连接与隔离 Chromium。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import sys

import pytest
from playwright.sync_api import sync_playwright

from omni.config import MCPConfig, MCPServerConfig, PROJECT_ROOT
from omni.tools.android.adb import AdbDevice
from omni.tools.mcp.client import MCPError, MCPManager


@pytest.mark.skipif(sys.platform != 'win32', reason='ConPTY 仅 Windows')
def test_twenty_owned_terminal_sessions_release_pool():
    from omni.channels.terminal import TerminalManager
    host = TerminalManager(max_sessions=1)
    try:
        for _ in range(20):
            session = host.open()
            with ThreadPoolExecutor(1) as reader:
                pending = reader.submit(session.read)
                try:
                    assert pending.result(timeout=5)
                finally:
                    assert host.close(session)
            assert host._count == 0 and not host._sessions
    finally:
        assert host.close_all()['complete']


def manager(tmp_path):
    return MCPManager(MCPConfig(servers={'demo': MCPServerConfig(command='fake')}), tmp_path/'logs')


def test_adb_global_and_explicit_device_arguments():
    calls = []
    def run(argv, timeout):
        calls.append(argv)
        return b'List of devices attached\nA\tdevice\nB\tdevice\n' if argv[-1] == 'devices' else b'1080x2400'
    device = AdbDevice(serial='B', adb_path='fake', runner=run)
    assert device.require_device() == 'B'
    assert device.screen_size('B') == (1080, 2400)
    assert calls[0] == ['fake', 'devices']
    assert calls[-1] == ['fake', '-s', 'B', 'shell', 'wm size']


def test_mcp_failed_close_retains_original_connection(tmp_path, monkeypatch):
    host = manager(tmp_path)
    original = host._connections['demo']
    monkeypatch.setattr(original, 'close', lambda *args: False)
    with pytest.raises(MCPError, match='替代连接'):
        host.reset('demo')
    assert host._connections['demo'] is original
    assert not host.close(wait_s=0)


def test_mcp_closed_manager_cannot_reset_or_dispatch(tmp_path):
    host = manager(tmp_path)
    assert host.close()
    with pytest.raises(MCPError, match='关闭'): host.reset('demo')
    with pytest.raises(MCPError, match='关闭'): host.list_tools('demo')
    with pytest.raises(MCPError, match='关闭'): host.call('demo', 'echo', {}, readonly=False)


def test_mcp_close_during_reset_never_constructs_replacement(tmp_path, monkeypatch):
    host = manager(tmp_path)
    original = host._connections['demo']
    entered, release = threading.Event(), threading.Event()
    def close(*args):
        entered.set()
        assert release.wait(2)
        return True
    monkeypatch.setattr(original, 'close', close)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(host.reset, 'demo')
        assert entered.wait(1)
        assert not host.close(wait_s=0.01)
        release.set()
        with pytest.raises(MCPError, match='关闭'): future.result(timeout=2)
    assert host._connections['demo'] is original
    assert host.close()


def test_terminal_reopens_after_close_and_socket_failure():
    source = (PROJECT_ROOT/'src/omni/channels/web_static/index.html').read_text(encoding='utf-8')
    start = source.index('let term=null,termWS=null,termFit=null,termSessionOpen=false;')
    end = source.index('// ---------- 安卓手机', start)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="term-modal"><div id="terminal"></div></div><button id="open-terminal"></button>')
            page.evaluate('''() => {
                window.$=s=>document.querySelector(s);window.sockets=[];
                window.Terminal=class {constructor(){this.cols=100;this.rows=30}loadAddon(){}open(){}onData(){}onResize(){}write(){}writeln(){}};
                window.FitAddon={FitAddon:class {fit(){}}};
                window.WebSocket=class {constructor(){this.readyState=1;sockets.push(this)}send(){}close(){this.readyState=3}};
            }''')
            page.add_script_tag(content=source[start:end])
            assert page.evaluate('''() => {
                for(let i=0;i<20;i++){openTerminal();closeTerminal()}
                openTerminal();sockets.at(-1).readyState=3;openTerminal();
                return sockets.length;
            }''') == 22
        finally:
            browser.close()
