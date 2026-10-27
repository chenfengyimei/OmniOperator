"""安卓工具集（toolset=android）：观察 SAFE，动作 CONFIRM。

使用纪律（范凯 skill 的核心，xuexi/NOTES.md §3.1）：
- 每轮只做一个改变设备的动作，动作后必须重新观察（refresh_tree）再决定下一步；
- 首选 tap_element(idx)（索引），裸坐标是兜底；
- 中文输入走 ADBKeyboard 广播，底层保证恢复原输入法。
"""
from __future__ import annotations

from typing import Any

from ..registry import Risk, Tool, ToolRegistry
from .driver import AndroidDriver


def register_android_tools(registry: ToolRegistry, driver: AndroidDriver) -> None:

    def android_status(_args: dict[str, Any]) -> str:
        try:
            devices = driver._adb.devices()
        except Exception as exc:  # noqa: BLE001 —— adb 不在 PATH 等
            return f"错误：{exc}（先安装 Android Platform-Tools 并把 adb 加入 PATH）"
        if not devices:
            return "没有检测到安卓设备（USB 调试开、授权弹窗点允许）"
        return "设备：" + ", ".join(
            f"{d['serial']}（{d['state']}）" for d in devices
        )

    def android_tree(_args: dict[str, Any]) -> str:
        try:
            return driver.refresh_tree()
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    def android_tap_element(args: dict[str, Any]) -> str:
        try:
            idx = int(args["idx"])
        except (KeyError, TypeError, ValueError):
            return "错误：idx 必须是整数（从 screen_tree 输出里取 [idx]）"
        try:
            return driver.tap_element(idx)
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    def android_tap_coords(args: dict[str, Any]) -> str:
        try:
            x, y = int(args["x"]), int(args["y"])
        except (KeyError, TypeError, ValueError):
            return "错误：x/y 必须是整数"
        try:
            return driver.tap_coords(x, y)
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    def android_swipe(args: dict[str, Any]) -> str:
        direction = str(args.get("direction", "up")).strip().lower()
        try:
            return driver.swipe(direction)
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    def android_key(args: dict[str, Any]) -> str:
        try:
            return driver.key(str(args["keycode"]).strip().upper())
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    def android_type(args: dict[str, Any]) -> str:
        text = str(args.get("text", ""))
        if not text:
            return "错误：text 不能为空"
        try:
            return driver.type_text(text)
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    def android_screenshot(_args: dict[str, Any]) -> str:
        try:
            path = driver.screenshot()
            return f"截图已保存：{path}"
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    registry.register(
        Tool(
            name="android_status",
            description="检查安卓设备连接状态（无设备时提示怎么连）",
            parameters={"type": "object", "properties": {}, "required": []},
            func=android_status,
            toolset="android",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="android_screen_tree",
            description=(
                "观察安卓手机当前界面：返回带 [idx] 索引的可交互元素树。"
                "点击前必须先调用它；动作后也必须再调一次核对结果"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            func=android_tree,
            toolset="android",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="android_screenshot",
            description="截取安卓手机当前屏幕，保存为图片文件",
            parameters={"type": "object", "properties": {}, "required": []},
            func=android_screenshot,
            toolset="android",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="android_tap_element",
            description=(
                "点击界面上第 idx 号元素（索引来自 android_screen_tree）。"
                "改变设备状态的操作：每轮只做一个，做完必须重新观察核对"
            ),
            parameters={
                "type": "object",
                "properties": {"idx": {"type": "integer", "description": "screen_tree 里的 [idx]"}},
                "required": ["idx"],
            },
            func=android_tap_element,
            toolset="android",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="android_tap_coords",
            description="按坐标点击（兜底手段：优先用 android_tap_element）。需用户确认",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                "required": ["x", "y"],
            },
            func=android_tap_coords,
            toolset="android",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="android_swipe",
            description="滑动屏幕：direction 为 up（看更多）/ down / back（返回键）。需用户确认",
            parameters={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "back"]}
                },
                "required": ["direction"],
            },
            func=android_swipe,
            toolset="android",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="android_key",
            description="按系统按键（KEYCODE_BACK/KEYCODE_HOME 等，白名单校验）。需用户确认",
            parameters={
                "type": "object",
                "properties": {
                    "keycode": {"type": "string", "description": "如 KEYCODE_BACK"}
                },
                "required": ["keycode"],
            },
            func=android_key,
            toolset="android",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="android_type",
            description=(
                "向当前焦点输入框输入文字（自动支持中文/Emoji，经 ADBKeyboard 广播）。"
                "需用户确认；输入后用 screen_tree 核对是否生效"
            ),
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要输入的文字"}},
                "required": ["text"],
            },
            func=android_type,
            toolset="android",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
