"""浏览器工具集（toolset=browser）：读 SAFE，写 CONFIRM。

风险设计（docs/ARCHITECTURE.md §6 P3 行）：
- 读操作（navigate/read/screenshot）SAFE 直通；
- 写操作（click/type）CONFIRM——过审批闸（CLI 问终端、Web 弹横幅、无人值守拒）
  且先扣每日写预算（WriteBudget），两道闸都过才执行；
- 写后自动回读页面片段（driver 层内置），agent 拿到的结果里带验证信息。
"""
from __future__ import annotations

from typing import Any

from ...config import Asr  # noqa: F401 —— 保持导入结构与其它能力域一致（占位）
from ..registry import Risk, Tool, ToolRegistry
from .budget import WriteBudget
from .driver import BrowserDriver, BrowserError


def register_browser_tools(
    registry: ToolRegistry, driver: BrowserDriver, budget: WriteBudget
) -> None:
    _TARGET_DESC = (
        "元素定位：'text=登录' 按可见文字找；'css=#submit' 按选择器找；"
        "裸文字等同于 text="
    )

    def browser_navigate(args: dict[str, Any]) -> str:
        url = str(args.get("url", "")).strip()
        if not url:
            return "错误：url 不能为空"
        title = driver.navigate(url)
        return f"已打开：{url}\n页面标题：{title}"

    def browser_read(_args: dict[str, Any]) -> str:
        return driver.read()

    def browser_screenshot(_args: dict[str, Any]) -> str:
        path = driver.screenshot()
        return f"截图已保存：{path}（当前无法直接看图，可让用户打开检查，或用 read 读文本）"

    def browser_click(args: dict[str, Any]) -> str:
        target = str(args.get("target", "")).strip()
        if not target:
            return "错误：target 不能为空"
        ok, quota = budget.try_consume()
        if not ok:
            return f"错误：{quota}"
        try:
            verify = driver.click(target)
            return f"已点击「{target}」（{quota}）。\n{verify}"
        except BrowserError as exc:
            return f"错误：{exc}"

    def browser_type(args: dict[str, Any]) -> str:
        target = str(args.get("target", "")).strip()
        text = str(args.get("text", ""))
        if not target:
            return "错误：target 不能为空"
        ok, quota = budget.try_consume()
        if not ok:
            return f"错误：{quota}"
        try:
            verify = driver.type_text(target, text)
            return f"已向「{target}」输入内容（{quota}）。\n{verify}"
        except BrowserError as exc:
            return f"错误：{exc}"

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
            description="截取当前页面截图保存到 data/browser_shots/latest.png",
            parameters={"type": "object", "properties": {}, "required": []},
            func=browser_screenshot,
            toolset="browser",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="browser_click",
            description=(
                "点击页面元素（需用户确认，且消耗每日写额度）。写操作后自动回读页面片段"
                "用于核对效果"
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "description": _TARGET_DESC}},
                "required": ["target"],
            },
            func=browser_click,
            toolset="browser",
            risk=Risk.CONFIRM,
        )
    )
    registry.register(
        Tool(
            name="browser_type",
            description=(
                "向输入框填入文字（需用户确认，且消耗每日写额度）。写操作后自动回读页面片段"
            ),
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
        )
    )
