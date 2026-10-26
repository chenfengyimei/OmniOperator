"""Playwright 浏览器驱动：持久化登录态 profile + 线程串行 + 拟人节奏。

- 持久 profile（data/browser_profile）：登录一次B站后 Cookie 常驻；
- 所有操作一把可重入锁：CLI/Web/心跳并发调用被安全串行化；
- Playwright slow_mo + 动作间随机小延迟：既是拟人节奏也是稳定性余量；
- 懒启动：首次用到才拉起浏览器（省资源）；
- 写操作（click/type）后自动回读页面片段——"写后复核"（NOTES §4.3）。
"""
from __future__ import annotations

import random
import threading
import time
import datetime as dt
from pathlib import Path
from typing import Any

from ...config import DATA_DIR


class BrowserError(RuntimeError):
    """浏览器操作失败（未装浏览器/页面超时/元素找不到）。"""


class BrowserDriver:
    def __init__(
        self,
        *,
        headless: bool = False,
        slow_mo_ms: int = 120,
        profile_dir: Path | None = None,
    ) -> None:
        self._headless = headless
        self._slow_mo = slow_mo_ms
        self._profile_dir = profile_dir or (DATA_DIR / "browser_profile")
        self._shots_dir = DATA_DIR / "browser_shots"
        self._lock = threading.RLock()
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None

    # ---------- 生命周期 ----------

    @property
    def running(self) -> bool:
        return self._page is not None

    def _ensure_page(self):
        with self._lock:
            if self._page is not None:
                return self._page
            from playwright.sync_api import sync_playwright  # 懒导入

            try:
                self._pw = sync_playwright().start()
                self._context = self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    headless=self._headless,
                    slow_mo=self._slow_mo,
                    viewport={"width": 1280, "height": 800},
                    locale="zh-CN",
                )
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else self._context.new_page()
                )
                return self._page
            except Exception as exc:  # noqa: BLE001 —— 给可操作的提示
                message = str(exc)
                if "Executable doesn't exist" in message:
                    raise BrowserError(
                        "Chromium 未安装：先运行 `uv run playwright install chromium`"
                    ) from exc
                self._page = None
                raise BrowserError(f"浏览器启动失败：{exc}") from exc

    def _sync_active_page(self) -> None:
        """点击可能开新页签：跟随到最新的页签（登录跳转常见）。"""
        with self._lock:
            pages = getattr(self._context, "pages", []) or []
            if pages and self._page in pages:
                self._page = pages[-1]

    def close(self) -> None:
        with self._lock:
            for closer in (
                getattr(self._context, "close", None),
                getattr(self._pw, "stop", None),
            ):
                if closer:
                    try:
                        closer()
                    except Exception:  # noqa: BLE001 —— 关闭失败不影响退出
                        pass
            self._page = self._context = self._pw = None

    # ---------- 读操作 ----------

    def navigate(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        with self._lock:
            page = self._ensure_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._sync_active_page()
                return page.title()
            except BrowserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"打开 {url} 失败：{exc}") from exc

    def title(self) -> str:
        with self._lock:
            return self._ensure_page().title()

    def read(self, max_chars: int = 4000) -> str:
        with self._lock:
            page = self._ensure_page()
            try:
                text = page.evaluate("document.body.innerText")
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"读取页面文本失败：{exc}") from exc
        text = (text or "").strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n…（共 {len(text)} 字，已截断）"
        return text or "（页面没有可读文本）"

    def screenshot(self) -> Path:
        with self._lock:
            page = self._ensure_page()
            self._shots_dir.mkdir(parents=True, exist_ok=True)
            path = self._shots_dir / "latest.png"
            try:
                page.screenshot(path=str(path))
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"截图失败：{exc}") from exc
            return path

    # ---------- 写操作（CONFIRM 级，工具层还会过审批与预算） ----------

    def click(self, target: str) -> str:
        with self._lock:
            page = self._ensure_page()
            locator = self._resolve(page, target)
            self._human_pause()
            try:
                locator.click(timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except BrowserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"点击「{target}」失败：{exc}") from exc
            self._sync_active_page()
        return self._verify_snippet()

    def type_text(self, target: str, text: str) -> str:
        with self._lock:
            page = self._ensure_page()
            locator = self._resolve(page, target)
            self._human_pause()
            try:
                locator.fill(text, timeout=10000)
            except BrowserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"向「{target}」输入失败：{exc}") from exc
        return self._verify_snippet()

    # ---------- 内部 ----------

    def _resolve(self, page, target: str):
        """target 支持三种形式：text=可见文字 / css=选择器 / 裸文本（按文字找）。"""
        target = (target or "").strip()
        if not target:
            raise BrowserError("target 不能为空")
        if target.startswith("css="):
            return page.locator(target[4:]).first
        if target.startswith("text="):
            return page.get_by_text(target[5:]).first
        return page.get_by_text(target).first

    def _human_pause(self) -> None:
        """动作间拟人停顿（叠加在 slow_mo 之上的随机性）。"""
        time.sleep(random.uniform(0.3, 0.8))

    def _verify_snippet(self) -> str:
        """写后复核：返回页面当前文本片段，供 agent 自检动作是否生效。"""
        try:
            snippet = self.read(max_chars=300).replace("\n", " ")
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            return f"[{stamp} 操作后页面] {snippet}"
        except BrowserError as exc:
            return f"（写后复核失败：{exc}）"
