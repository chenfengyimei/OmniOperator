"""浏览器工具集（toolset=browser）：读 SAFE，写 CONFIRM。

风险设计（docs/v/reference/ARCHITECTURE.md §6 P3 行）：
- 读操作（navigate/read/screenshot）SAFE 直通；
- 写操作（click/type）CONFIRM——过审批闸（CLI 问终端、Web 弹横幅、无人值守拒）
  且先扣每日写预算（WriteBudget），两道闸都过才执行；
- 写后自动回读页面片段（driver 层内置），agent 拿到的结果里带验证信息。

结果契约（D09 收口）：写成功=applied（写后回读进 verification）；
BrowserUnknown 与动作内的 BrowserError=unknown——点击后 load-state 等待失败时
**点击可能已发出**，无法从异常区分，诚实按副作用未知处理，不盲目重试；
BrowserBusy（入队前）/预算拒绝=确定未执行。
"""

from __future__ import annotations

from typing import Any

from ...config import Asr  # noqa: F401 —— 保持导入结构与其它能力域一致（占位）
from ..registry import Risk, SessionRef, Tool, ToolRegistry
from ..result import SIDE_EFFECT_APPLIED, ToolResult
from ..runctx import RunRef
from ..shots import save_screenshot
from .budget import WriteBudget
from .driver import BrowserBusy, BrowserDriver, BrowserError, BrowserUnknown


def register_browser_tools(
    registry: ToolRegistry,
    driver: BrowserDriver,
    budget: WriteBudget,
    *,
    artifacts: Any = None,
    session_ref: SessionRef | None = None,
    run_ref: RunRef | None = None,
    can_view: bool = False,
) -> None:
    _TARGET_DESC = "元素定位：'text=登录' 按可见文字找；'css=#submit' 按选择器找；裸文字等同于 text="

    def browser_navigate(args: dict[str, Any]) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult.fail("错误：url 不能为空", error_code="bad_arguments")
        try:
            title = driver.navigate(url)
        except BrowserUnknown as exc:
            return ToolResult.unknown(f"错误：{exc}", error_code="browser_unknown")
        except BrowserBusy as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="browser_busy", retryable=True)
        except BrowserError as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="browser_failed")
        return ToolResult.ok(f"已打开：{url}\n页面标题：{title}")

    def browser_read(_args: dict[str, Any]) -> ToolResult:
        try:
            return ToolResult.ok(driver.read())
        except BrowserUnknown as exc:
            return ToolResult.unknown(f"错误：{exc}", error_code="browser_unknown")
        except BrowserBusy as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="browser_busy", retryable=True)
        except BrowserError as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="browser_failed")

    def browser_screenshot(_args: dict[str, Any]) -> ToolResult:
        if artifacts is not None:
            try:
                data = driver.screenshot_bytes()
            except BrowserUnknown as exc:
                return ToolResult.unknown(f"错误：{exc}", error_code="browser_unknown")
            except BrowserBusy as exc:
                return ToolResult.fail(f"错误：{exc}", error_code="browser_busy", retryable=True)
            except BrowserError as exc:
                return ToolResult.fail(f"错误：{exc}", error_code="browser_failed")
            return ToolResult.ok(
                save_screenshot(
                    artifacts,
                    data,
                    session_id=(session_ref.get() if session_ref else None),
                    run_id=(run_ref.get().run_id if run_ref is not None and run_ref.get() else None),
                    kind="screenshot",
                    source="browser_screenshot",
                    can_view=can_view,
                )
            )
        path = driver.screenshot()
        return ToolResult.ok(f"截图已保存：{path}（当前无法直接看图，可让用户打开检查，或用 read 读文本）")

    def browser_click(args: dict[str, Any]) -> ToolResult:
        target = str(args.get("target", "")).strip()
        if not target:
            return ToolResult.fail("错误：target 不能为空", error_code="bad_arguments")
        ok, quota = budget.try_consume()
        if not ok:
            return ToolResult.fail(f"错误：{quota}", error_code="budget_exhausted")
        try:
            verify = driver.click(target)
        except BrowserUnknown as exc:
            return ToolResult.unknown(f"错误：{exc}", error_code="browser_unknown")
        except BrowserBusy as exc:
            # 排队被拒：动作从未进入所有者线程，预算不回滚但副作用确定未发生
            return ToolResult.fail(f"错误：{exc}", error_code="browser_busy", retryable=True)
        except BrowserError as exc:
            # 定位失败与"点击后等待失败"在这里分不开：一律按副作用未知，不盲目重试
            return ToolResult.unknown(f"错误：{exc}", error_code="browser_action_unknown")
        return ToolResult.ok(
            f"已点击「{target}」（{quota}）。\n{verify}",
            side_effect_state=SIDE_EFFECT_APPLIED,
            verification=verify,
        )

    def browser_type(args: dict[str, Any]) -> ToolResult:
        target = str(args.get("target", "")).strip()
        text = str(args.get("text", ""))
        if not target:
            return ToolResult.fail("错误：target 不能为空", error_code="bad_arguments")
        ok, quota = budget.try_consume()
        if not ok:
            return ToolResult.fail(f"错误：{quota}", error_code="budget_exhausted")
        try:
            verify = driver.type_text(target, text)
        except BrowserUnknown as exc:
            return ToolResult.unknown(f"错误：{exc}", error_code="browser_unknown")
        except BrowserBusy as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="browser_busy", retryable=True)
        except BrowserError as exc:
            return ToolResult.unknown(f"错误：{exc}", error_code="browser_action_unknown")
        return ToolResult.ok(
            f"已向「{target}」输入内容（{quota}）。\n{verify}",
            side_effect_state=SIDE_EFFECT_APPLIED,
            verification=verify,
        )

    registry.register(
        Tool(
            name="browser_navigate",
            description="用浏览器打开网页（持久登录态，登录过的网站保持会话）。返回页面标题",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "网址"}},
                "required": ["url"],
            },
            func=browser_navigate,
            toolset="browser",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="browser_read",
            description="读取当前页面的可见文本（最多 4000 字）",
            parameters={"type": "object", "properties": {}, "required": []},
            func=browser_read,
            toolset="browser",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="browser_screenshot",
            description="截取当前页面截图，存为产物 [artifact:id]（开启 vision 后模型可直接看图）",
            parameters={"type": "object", "properties": {}, "required": []},
            func=browser_screenshot,
            toolset="browser",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="browser_click",
            description=("点击页面元素（需用户确认，且消耗每日写额度）。写操作后自动回读页面片段用于核对效果"),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "description": _TARGET_DESC}},
                "required": ["target"],
            },
            func=browser_click,
            toolset="browser",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="browser_type",
            description=("向输入框填入文字（需用户确认，且消耗每日写额度）。写操作后自动回读页面片段"),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": _TARGET_DESC},
                    "text": {"type": "string", "description": "要输入的内容"},
                },
                "required": ["target", "text"],
            },
            func=browser_type,
            toolset="browser",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
