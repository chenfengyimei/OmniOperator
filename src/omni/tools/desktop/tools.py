from __future__ import annotations

import json

from ..registry import Risk, Tool, ToolRegistry
from ..result import ToolResult
from ..shots import save_screenshot


def register_desktop_tools(registry: ToolRegistry, driver, *, artifacts=None, session_ref=None, run_ref=None, can_view=False):
    def session():
        return session_ref.get() if session_ref else None

    def windows(args):
        return json.dumps(driver.windows(), ensure_ascii=False)

    def observe(args):
        return json.dumps(driver.observe(int(args["window"]), session_id=session()), ensure_ascii=False)

    def action(args):
        run = run_ref.get() if run_ref else None
        return driver.action(str(args["observation"]), str(args["element"]), str(args["action"]),
                             str(args.get("text", "")), session_id=session(), cancel=getattr(run, "cancel_token", None),
                             preview={"window_title": args["window_title"], "application": args["application"]})

    def screenshot(args):
        data = driver.screenshot(str(args["observation"]), session_id=session())
        run = run_ref.get() if run_ref else None
        return save_screenshot(artifacts, data, session_id=session(), run_id=getattr(run, "run_id", None),
                               kind="desktop", source="UIAutomation/PrintWindow", can_view=can_view)

    specs = [
        ("desktop_windows", "列出用户允许应用的窗口与进程身份；不抢焦点", {}, [], windows, Risk.SAFE),
        ("desktop_observe", "读取窗口可访问性树和能力，返回后续动作必须携带的 observation", {"window": {"type": "integer"}}, ["window"], observe, Risk.SAFE),
        ("desktop_screenshot", "按 observation 截取目标窗口并存为产物；不上传其它窗口", {"observation": {"type": "string"}}, ["observation"], screenshot, Risk.SAFE),
        ("desktop_action", "人工确认后执行一次后台语义动作：set_value 覆盖文本、invoke 激活、select 选择标签。审批必须展示观察中的窗口标题与应用完整路径；效果未知不重试，不支持前台接管",
         {"observation": {"type": "string"}, "window_title": {"type": "string"}, "application": {"type": "string"}, "element": {"type": "string"}, "action": {"enum": ["set_value", "invoke", "select"], "type": "string"}, "text": {"type": "string"}},
         ["observation", "window_title", "application", "element", "action"], action, Risk.CONFIRM),
    ]
    for name, description, properties, required, func, risk in specs:
        registry.register(Tool(name=name, description=description,
                               parameters={"type": "object", "properties": properties, "required": required},
                               func=func, toolset="desktop", risk=risk, requires_human=risk == Risk.CONFIRM))
