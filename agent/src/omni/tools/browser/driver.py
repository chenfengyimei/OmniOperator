"""Playwright 浏览器驱动：单一所有者线程 + profile 跨进程租约 + 拟人节奏。

为什么要"所有者线程"（A11）：Playwright 的同步 API 绑定在创建它的线程上（greenlet 机制）。
以前的实现在 web 的线程池里被不同线程调用，`threading.RLock` 只保证不并发，**不改变线程归属**——
下一次调用换个线程就报错或行为异常。现在所有 Playwright 调用（启动、读、点、关）都在同一条
专用线程里执行，调用方通过有界队列投递任务并等结果。

三条边界：

1. **等待超时不等于动作被取消**。超时返回 `BrowserUnknown`，明确说"可能仍在进行、状态未知"，
   不假装失败、也不自动重试；要不要继续由人或上层决定。
2. **队列有界**。浏览器卡住时继续堆任务只会把内存和时间一起吃掉，满了直接 `BrowserBusy`。
3. **profile 跨进程互斥**。同一个 `user_data_dir` 被两个进程同时打开会互相破坏登录态，
   用 `state.db` 的租约互斥，拿不到就带持有者报错。

仍然保留：持久 profile（登录态常驻）、slow_mo + 随机停顿（拟人）、写后回读复核。
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import queue
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ...config import DATA_DIR


class BrowserError(RuntimeError):
    """浏览器操作失败（未装浏览器/页面超时/元素找不到）。"""


class BrowserBusy(BrowserError):
    """浏览器正忙：执行队列已满，或 profile 被另一个进程占用。"""


class BrowserUnknown(BrowserError):
    """等不到结果：动作可能仍在进行，状态未知（不是"失败"，也不能当"没发生"）。"""


class ProfileLease(Protocol):
    """profile 租约（由 bootstrap 注入，通常来自 `state.db`）。"""

    def acquire(self, resource: str, *, owner: str, ttl_s: float) -> str: ...

    def renew(self, token: str, ttl_s: float) -> bool: ...

    def release(self, token: str) -> bool: ...


@dataclasses.dataclass
class _Call:
    fn: Callable[[], Any]
    done: threading.Event = dataclasses.field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None


class _OwnerThread:
    """一条专用线程 + 有界队列：所有浏览器调用都排到这里顺序执行。"""

    def __init__(self, *, queue_size: int = 8, name: str = "browser-owner") -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, int(queue_size)))
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        # 线程 id 必须在**调用方能看见之前**确定：若等到线程体里才赋值，
        # start() 返回时新线程可能还没跑到那行，此刻的首个调用会误判
        # "已经在所有者线程"而在调用方线程直接执行 Playwright——A11 的
        # "所有 Playwright 调用同一条线程"不变量就此被破坏（实测偶发）。
        # 用启动握手换确定性：线程一启动就报出自己的 id。
        self._started = threading.Event()
        self.thread_id: int | None = None
        self._thread.start()
        self._started.wait(timeout=5.0)

    def _loop(self) -> None:
        self.thread_id = threading.get_ident()
        self._started.set()
        while True:
            call = self._queue.get()
            if call is None:  # 停止信号
                return
            try:
                call.value = call.fn()
            except BaseException as exc:  # noqa: BLE001 —— 原样交回调用方
                call.error = exc
            finally:
                call.done.set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def run(self, fn: Callable[[], Any], *, timeout_s: float) -> Any:
        if threading.get_ident() == self.thread_id:
            return fn()  # 已经在所有者线程里（关闭流程会走到这里）
        if not self.alive:
            raise BrowserError("浏览器操作线程已退出（上一次操作崩溃？）；请重启工作台后再试")
        call = _Call(fn=fn)
        try:
            self._queue.put_nowait(call)
        except queue.Full:
            raise BrowserBusy(
                f"浏览器正忙：已有 {self.depth} 个操作在排队；等当前动作结束后再试"
            ) from None
        if not call.done.wait(max(0.1, float(timeout_s))):
            raise BrowserUnknown(
                f"等待 {timeout_s:.0f}s 仍未返回：动作可能仍在进行，状态未知。"
                "不要重复执行同一个写操作；先 read/截图确认当前页面再决定"
            )
        if call.error is not None:
            raise call.error
        return call.value

    def stop(self, *, timeout_s: float = 3.0) -> bool:
        """投递停止信号并等待线程退出；返回是否真的退出了。"""
        with contextlib.suppress(queue.Full):
            self._queue.put(None, timeout=timeout_s)
        self._thread.join(timeout_s)
        return not self._thread.is_alive()


class BrowserDriver:
    def __init__(
        self,
        *,
        headless: bool = False,
        slow_mo_ms: int = 120,
        profile_dir: Path | None = None,
        shots_dir: Path | None = None,
        action_timeout_s: float = 60.0,
        close_timeout_s: float = 20.0,
        queue_size: int = 8,
        lease: ProfileLease | None = None,
        lease_ttl_s: float = 1800.0,
        lease_owner: str | None = None,
    ) -> None:
        self._headless = headless
        self._slow_mo = slow_mo_ms
        self._profile_dir = profile_dir or (DATA_DIR / "browser_profile")
        self._shots_dir = shots_dir or (DATA_DIR / "browser_shots")
        self._action_timeout = float(action_timeout_s)
        self._close_timeout = float(close_timeout_s)
        self._queue_size = int(queue_size)
        self._lease = lease
        self._lease_ttl = float(lease_ttl_s)
        self._lease_owner = lease_owner or "browser"
        self._lease_token: str | None = None
        self._owner: _OwnerThread | None = None
        # 保护 owner 线程的"取或建"：并发首调不得各建一条线程（A11 不变量）
        self._owner_lock = threading.Lock()
        self._closed = False
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None

    # ---------- 生命周期 ----------

    @property
    def profile_dir(self) -> Path:
        return self._profile_dir

    @property
    def resource(self) -> str:
        """租约键：按 profile 绝对路径区分（两个实例用不同 profile 就不该互斥）。"""
        return f"browser_profile:{self._profile_dir.resolve()}"

    @property
    def running(self) -> bool:
        """仅作提示：真正的判据在所有者线程里。"""
        return self._page is not None

    @property
    def thread_id(self) -> int | None:
        return self._owner.thread_id if self._owner is not None else None

    @property
    def queue_depth(self) -> int:
        return self._owner.depth if self._owner is not None else 0

    def _run(self, fn: Callable[[], Any], *, timeout_s: float | None = None) -> Any:
        if self._closed:
            raise BrowserError("浏览器已关闭；请重启工作台或重新打开一次浏览器")
        # owner 线程的创建必须串行：并发首调时若各自看到 owner is None 就会
        # **各建一条线程**，Playwright 调用于是分散在多条线程上（A11 不变量被破坏）。
        # 锁内只做"取或建"，实际调用仍在 owner 线程里排队执行。
        with self._owner_lock:
            owner = self._owner
            if owner is None or not owner.alive:
                owner = self._owner = _OwnerThread(queue_size=self._queue_size)
        return owner.run(fn, timeout_s=self._action_timeout if timeout_s is None else timeout_s)

    def close(self, *, wait_s: float | None = None) -> str:
        """有界等待关闭，返回可读结论——超时就说"可能残留"，不假装已关闭。"""
        wait = self._close_timeout if wait_s is None else float(wait_s)
        self._closed = True
        owner = self._owner
        if owner is None:
            self._release_lease()
            return "浏览器未启动（无需关闭）"
        try:
            owner.run(self._shutdown_here, timeout_s=wait)
        except BrowserUnknown as exc:
            return f"关闭超时：浏览器可能仍在运行（{exc}）"
        except BrowserError as exc:
            return f"关闭失败：{exc}"
        if not owner.stop(timeout_s=2.0):
            return "关闭超时：负责浏览器的线程未退出，可能仍有残留进程"
        self._owner = None
        return "浏览器已关闭"

    def _shutdown_here(self) -> None:
        """在所有者线程里执行真正的关闭（Playwright 的对象只能在自己线程里关）。"""
        failures = []
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer:
                try:
                    closer()
                except Exception as exc:
                    failures.append(type(exc).__name__)
        if failures:
            raise BrowserError("浏览器退出未确认：" + ",".join(failures))
        self._page = self._context = self._pw = None
        self._release_lease()

    def _acquire_lease(self) -> None:
        if self._lease is None or self._lease_token is not None:
            return
        try:
            self._lease_token = self._lease.acquire(
                self.resource, owner=self._lease_owner, ttl_s=self._lease_ttl
            )
        except Exception as exc:  # noqa: BLE001 —— 适配器用什么异常都行，统一转成忙
            raise BrowserBusy(f"浏览器 profile 不可用：{exc}") from exc

    def _renew_lease(self) -> None:
        if self._lease is None or self._lease_token is None:
            return
        try:
            ok = self._lease.renew(self._lease_token, self._lease_ttl)
        except Exception as exc:  # noqa: BLE001
            raise BrowserBusy(f"浏览器 profile 租约续期失败：{exc}") from exc
        if not ok:
            self._lease_token = None
            raise BrowserBusy("浏览器 profile 租约已失效（可能被其它进程接手）；请重新打开一次")

    def _release_lease(self) -> None:
        token, self._lease_token = self._lease_token, None
        if token and self._lease is not None:
            try:
                self._lease.release(token)
            except Exception:  # noqa: BLE001 —— 释放失败不该掩盖真正错误
                pass

    # ---------- 页面 ----------

    def _ensure_page(self):
        if threading.get_ident() != (self._owner.thread_id if self._owner else None):
            raise BrowserError("内部错误：Playwright 调用不在所有者线程上（A11 回归）")
        self._renew_lease()
        if self._page is not None:
            return self._page
        self._acquire_lease()
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
            self._page = self._context = self._pw = None
            self._release_lease()  # 启动失败要把 profile 让出来，否则下次永远"被占用"
            if "Executable doesn't exist" in message:
                raise BrowserError(
                    "Chromium 未安装：先运行 `uv run playwright install chromium`"
                ) from exc
            raise BrowserError(f"浏览器启动失败：{exc}") from exc

    def _sync_active_page(self) -> None:
        """点击可能开新页签：跟随到最新的页签（登录跳转常见）。"""
        pages = getattr(self._context, "pages", []) or []
        if pages and self._page in pages:
            self._page = pages[-1]

    # ---------- 读操作 ----------

    def navigate(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        def action() -> str:
            page = self._ensure_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._sync_active_page()
                return page.title()
            except BrowserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"打开 {url} 失败：{exc}") from exc

        return self._run(action)

    def title(self) -> str:
        return self._run(lambda: self._ensure_page().title())

    def read(self, max_chars: int = 4000) -> str:
        def action() -> str:
            page = self._ensure_page()
            try:
                text = page.evaluate("document.body.innerText")
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"读取页面文本失败：{exc}") from exc
            return (text or "").strip()

        text = self._run(action)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n…（共 {len(text)} 字，已截断）"
        return text or "（页面没有可读文本）"

    def screenshot(self) -> Path:
        def action() -> Path:
            page = self._ensure_page()
            shots = self._shots_dir or Path(".")
            shots.mkdir(parents=True, exist_ok=True)
            path = shots / "latest.png"
            try:
                page.screenshot(path=str(path))
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"截图失败：{exc}") from exc
            return path

        return self._run(action)

    def screenshot_bytes(self) -> bytes:
        """当前页面 PNG 字节（D15：产物存 ArtifactStore，不再只落一个路径）。"""

        def action() -> bytes:
            page = self._ensure_page()
            try:
                return page.screenshot()  # 不给 path → 返回 PNG bytes
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"截图失败：{exc}") from exc

        return self._run(action)

    # ---------- 写操作（CONFIRM 级，工具层还会过审批与预算） ----------

    def click(self, target: str) -> str:
        def action() -> None:
            page = self._ensure_page()
            locator = self._resolve(page, target)
            self._human_pause()  # 拟人停顿（在所有者线程里睡，不占调用方）
            try:
                locator.click(timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except BrowserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"点击「{target}」失败：{exc}") from exc
            self._sync_active_page()

        self._run(action)
        return self._verify_snippet()

    def type_text(self, target: str, text: str) -> str:
        def action() -> None:
            page = self._ensure_page()
            locator = self._resolve(page, target)
            self._human_pause()
            try:
                locator.fill(text, timeout=10000)
            except BrowserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"向「{target}」输入失败：{exc}") from exc

        self._run(action)
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
