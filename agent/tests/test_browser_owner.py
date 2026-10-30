"""浏览器所有权单测（D07 / A11）：所有调用同一条线程、队列有界、超时是"未知"、profile 跨进程互斥。

用假 `playwright.sync_api` 模块（monkeypatch 进 sys.modules）记录每次调用的线程 id，
这样能直接观察到 A11 的核心不变量；另有一条真实 Chromium 用例（未安装则 skip）。
"""
from __future__ import annotations

import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from omni.core.session import ResourceLeases
from omni.memory.state import StateStore
from omni.tools.browser.driver import (
    BrowserBusy,
    BrowserDriver,
    BrowserError,
    BrowserUnknown,
)

ROOT = Path(__file__).resolve().parents[1]


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.lock = threading.Lock()

    def record(self, op: str) -> int:
        tid = threading.get_ident()
        with self.lock:
            self.calls.append((op, tid))
        return tid

    def ops(self) -> list[str]:
        return [op for op, _tid in self.calls]

    def thread_ids(self) -> set[int]:
        return {tid for _op, tid in self.calls}


class _FakeLocator:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    @property
    def first(self):
        return self

    def click(self, timeout=None) -> None:
        self._rec.record("click")

    def fill(self, text, timeout=None) -> None:
        self._rec.record("fill")


class _FakePage:
    def __init__(self, rec: _Recorder, *, block: threading.Event | None = None,
                 delay: float = 0.0) -> None:
        self._rec = rec
        self._block = block
        self._delay = delay

    def goto(self, url, wait_until=None, timeout=None) -> None:
        self._rec.record("goto")
        if self._block is not None:
            self._block.wait(10)
        if self._delay:
            time.sleep(self._delay)

    def title(self) -> str:
        self._rec.record("title")
        return "假页面"

    def evaluate(self, _js) -> str:
        self._rec.record("evaluate")
        return "假页面正文"

    def screenshot(self, path) -> None:
        self._rec.record("screenshot")
        Path(path).write_bytes(b"png")

    def locator(self, _sel):
        self._rec.record("locator")
        return _FakeLocator(self._rec)

    def get_by_text(self, _text):
        self._rec.record("get_by_text")
        return _FakeLocator(self._rec)

    def wait_for_load_state(self, *_a, **_kw) -> None:
        self._rec.record("wait_load")


class _FakeContext:
    def __init__(self, page: _FakePage, rec: _Recorder) -> None:
        self.pages = [page]
        self._rec = rec

    def new_page(self):
        return self.pages[0]

    def close(self) -> None:
        self._rec.record("context.close")


class _FakeChromium:
    def __init__(self, page: _FakePage, rec: _Recorder) -> None:
        self._page = page
        self._rec = rec

    def launch_persistent_context(self, **_kw):
        self._rec.record("launch")
        return _FakeContext(self._page, self._rec)


class _FakePlaywright:
    def __init__(self, page: _FakePage, rec: _Recorder) -> None:
        self.chromium = _FakeChromium(page, rec)
        self._rec = rec

    def stop(self) -> None:
        self._rec.record("pw.stop")


@pytest.fixture()
def fake_playwright(monkeypatch):
    """把 playwright.sync_api 换成假模块；返回 (recorder, page_factory)。"""

    state: dict[str, object] = {}

    def install(**page_kwargs):
        rec = _Recorder()
        page = _FakePage(rec, **page_kwargs)
        module = types.ModuleType("playwright.sync_api")

        def sync_playwright():
            rec.record("sync_playwright")

            class _Starter:
                def start(self):
                    rec.record("start")
                    return _FakePlaywright(page, rec)

            return _Starter()

        module.sync_playwright = sync_playwright  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
        state["rec"], state["page"] = rec, page
        return rec, page

    install.__dict__["state"] = state  # 便于用例取回
    return install


def _driver(tmp_path, **kw) -> BrowserDriver:
    return BrowserDriver(
        profile_dir=tmp_path / "profile",
        shots_dir=tmp_path / "shots",
        action_timeout_s=kw.pop("action_timeout_s", 5.0),
        close_timeout_s=kw.pop("close_timeout_s", 5.0),
        **kw,
    )


def test_every_playwright_call_runs_on_one_owner_thread(tmp_path, fake_playwright):
    rec, _page = fake_playwright()
    driver = _driver(tmp_path)
    main_thread = threading.get_ident()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _i: driver.navigate("https://example.invalid"), range(4)))
        list(pool.map(lambda _i: driver.read(), range(4)))
        list(pool.map(lambda _i: driver.title(), range(2)))
        list(pool.map(lambda _i: driver.screenshot(), range(2)))
    driver.close()

    assert driver.thread_id is None  # 关闭后线程已回收
    owner_ids = rec.thread_ids()
    assert len(owner_ids) == 1, f"Playwright 调用分散在多个线程：{owner_ids}"
    owner = owner_ids.pop()
    assert owner != main_thread
    assert rec.calls[0][1] == owner and rec.calls[-1][1] == owner  # 启动与关闭也在同一条线程
    assert {"sync_playwright", "start", "launch", "context.close", "pw.stop"} <= set(rec.ops())


def test_queue_is_bounded_and_reports_busy(tmp_path, fake_playwright):
    block = threading.Event()
    fake_playwright(block=block)
    driver = _driver(tmp_path, queue_size=1)

    # 先让第一个操作占住所有者线程
    errors: list[BaseException] = []

    def run_first() -> None:
        try:
            driver.navigate("https://a")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=run_first)
    first.start()
    time.sleep(0.2)  # 让 goto 真正开始（阻塞在 block 上）

    second_result: list[BaseException] = []

    def run_second() -> None:
        try:
            driver.title()
        except BaseException as exc:  # noqa: BLE001
            second_result.append(exc)

    second = threading.Thread(target=run_second)
    second.start()
    time.sleep(0.1)  # 第二个进队列（容量 1）

    with pytest.raises(BrowserBusy, match="排队"):
        driver.title()  # 第三个装不下

    block.set()
    first.join(5)
    second.join(5)
    assert errors == []
    assert second_result == []  # 排队的那条最终正常执行
    driver.close()


def test_timeout_is_reported_as_unknown_not_cancelled(tmp_path, fake_playwright):
    rec, _page = fake_playwright(delay=0.6)
    driver = _driver(tmp_path, action_timeout_s=0.15)

    started = time.monotonic()
    with pytest.raises(BrowserUnknown, match="状态未知"):
        driver.navigate("https://slow.invalid")
    assert time.monotonic() - started < 0.6  # 调用方没有被一起拖住

    # 动作并没有被取消：它仍在所有者的线程里跑完
    time.sleep(0.8)
    assert "goto" in rec.ops()
    assert driver.title() == "假页面"  # 之后的调用照常可用（队列没有卡死）
    driver.close()


def test_close_reports_residual_when_an_action_is_stuck(tmp_path, fake_playwright):
    block = threading.Event()
    fake_playwright(block=block)
    driver = _driver(tmp_path)
    threading.Thread(target=driver.navigate, args=("https://stuck.invalid",), daemon=True).start()
    time.sleep(0.2)

    note = driver.close(wait_s=0.3)
    assert "超时" in note and "可能仍在运行" in note  # 不假装已关闭
    block.set()
    time.sleep(0.5)
    assert "已关闭" in driver.close(wait_s=3.0)


def test_profile_lease_is_exclusive_and_released_on_close(tmp_path, fake_playwright):
    fake_playwright()
    state = StateStore(tmp_path / "state.db")
    leases = ResourceLeases(state, owner="pid:111")
    driver = _driver(
        tmp_path,
        lease=leases,
        lease_owner="pid:111:browser",
        action_timeout_s=5.0,
    )

    # 另一个"进程"先占着这个 profile
    state.acquire_lease(driver.resource, "pid:999999", ttl_s=300)
    with pytest.raises(BrowserBusy, match="pid:999999"):
        driver.navigate("https://example.invalid")
    assert state.lease(driver.resource).owner == "pid:999999"

    state.release_lease(state.lease(driver.resource).token)
    assert driver.navigate("https://example.invalid") == "假页面"
    assert state.lease(driver.resource).owner == "pid:111:browser"
    driver.close()
    assert state.lease(driver.resource) is None  # 关闭即让出 profile
    state.close()


def test_profile_lease_blocks_a_real_second_process(tmp_path, fake_playwright):
    fake_playwright()
    state = StateStore(tmp_path / "state.db")
    driver = _driver(tmp_path, lease=ResourceLeases(state, owner="pid:111"))
    code = """
import sys, time
from pathlib import Path
from omni.memory.state import StateStore
db, resource, go = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
while not go.exists():
    time.sleep(0.005)
store = StateStore(db)
lease = store.acquire_lease(resource, "pid:999999", ttl_s=300)
print("GOT" if lease else "BUSY")
if lease:
    store.release_lease(lease.token)  # 探路而已：让父进程接着拿
"""
    import os
    import subprocess

    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    go = tmp_path / "go"
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(state.path), driver.resource, str(go)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    go.write_text("go", encoding="utf-8")
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    assert out.strip() == "GOT"  # 这时还没有人持有

    driver.navigate("https://example.invalid")  # 本进程拿到租约
    # 另一个进程再来就只能是 BUSY
    go2 = tmp_path / "go2"
    proc2 = subprocess.Popen(
        [sys.executable, "-c", code, str(state.path), driver.resource, str(go2)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    go2.write_text("go", encoding="utf-8")
    out2, err2 = proc2.communicate(timeout=60)
    assert proc2.returncode == 0, err2
    assert out2.strip() == "BUSY"
    driver.close()
    state.close()


def test_closed_driver_refuses_new_work(tmp_path, fake_playwright):
    fake_playwright()
    driver = _driver(tmp_path)
    assert driver.close() == "浏览器未启动（无需关闭）"
    with pytest.raises(BrowserError, match="已关闭"):
        driver.read()


def test_real_chromium_serves_calls_from_many_threads(tmp_path):
    """真实 Chromium：多个线程调用必须都成功（未装浏览器则 skip，并记录为待真实验收）。"""
    pytest.importorskip("playwright.sync_api")
    driver = BrowserDriver(
        headless=True,
        slow_mo_ms=0,
        profile_dir=tmp_path / "real_profile",
        shots_dir=tmp_path / "real_shots",
        action_timeout_s=60.0,
    )
    note = ""
    try:
        try:
            driver.title()
        except BrowserError as exc:
            pytest.skip(f"无法启动 Chromium（待真实验收）：{exc}")
        owner = driver.thread_id
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda mode: driver.read() if mode else driver.title(), [0, 1, 1]))
        assert len(results) == 3 and driver.thread_id == owner
    finally:
        note = driver.close()
    assert "已关闭" in note or "未启动" in note
