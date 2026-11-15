"""默认套件离线。真实设备/账号验收使用 scripts/manual_*，不由 pytest 偷跑。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from offline_guard import deny, install

if TYPE_CHECKING:
    from crash_safe_scheduler import CrashSafeLoadFileScheduling
    from xdist.remote import Producer

_directory = tempfile.TemporaryDirectory(prefix="omni-offline-")
_violations = Path(_directory.name) / "violations.txt"
os.environ["OMNI_TEST_OFFLINE"] = "1"
os.environ["OMNI_TEST_VIOLATIONS"] = str(_violations)
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
_tests = Path(__file__).resolve().parent
os.environ["PYTHONPATH"] = os.pathsep.join([str(_tests / "offline"), str(_tests), os.environ.get("PYTHONPATH", "")])
install()


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config):
    """自动并行保守使用最多 4 核；显式 -n 或 xdist 环境变量可覆盖。"""
    override = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if override is not None:
        try:
            workers = int(override)
        except ValueError as error:
            raise pytest.UsageError("PYTEST_XDIST_AUTO_NUM_WORKERS 必须是非负整数") from error
        if workers < 0:
            raise pytest.UsageError("PYTEST_XDIST_AUTO_NUM_WORKERS 必须是非负整数")
        return workers
    return min(4, os.cpu_count() or 1)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_make_scheduler(config: pytest.Config, log: Producer | None) -> CrashSafeLoadFileScheduling | None:
    if config.getoption("dist") != "loadfile":
        return None
    from crash_safe_scheduler import CrashSafeLoadFileScheduling

    return CrashSafeLoadFileScheduling(config, log)


@pytest.fixture(autouse=True)
def offline_model_gate(monkeypatch):
    from omni.llm import embedding, gateway

    transports = {"local": embedding._local_request, "api": embedding._api_request}
    before = _violations.stat().st_size if _violations.exists() else 0
    monkeypatch.setattr(gateway, "_completion", lambda **kwargs: deny("model.completion"))
    monkeypatch.setattr(embedding, "_local_request", lambda *args, **kwargs: deny("embedding.local"))
    monkeypatch.setattr(embedding, "_api_request", lambda *args, **kwargs: deny("embedding.api"))
    yield transports
    after = _violations.stat().st_size if _violations.exists() else 0
    assert after == before, "本用例或其子进程触发了网络/模型/真实数据隔离闸门；即使业务层捕获异常也不能算通过"


@pytest.fixture(scope="module")
def ui_browser():
    """同一测试文件复用 Chromium；每个用例自行创建、关闭独立 context。

    不跨文件持有同步 Playwright 的事件循环，避免干扰其他模块的 asyncio 测试。
    loadfile 调度保证一个文件只在一个 worker 上初始化浏览器。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def pytest_sessionfinish(session, exitstatus):
    if _violations.exists() and _violations.stat().st_size:
        session.exitstatus = 1
