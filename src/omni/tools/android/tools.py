"""安卓工具集（toolset=android）：观察 SAFE，动作 CONFIRM。

使用纪律（范凯 skill 的核心，xuexi/NOTES.md §3.1）：
- 观察绑定动作：screen_tree 返回观察身份 obs=<id>，tap_element 必须带它；
  点击前自动复核界面，不一致就不点（A12：旧 idx 绝不点新元素）；
- 每轮只做一个改变设备的动作；tap 后工具直接返回点击后的新观察；
- 首选 tap_element(idx, observation)（索引），裸坐标是兜底；
- 中文输入走 ADBKeyboard 广播，底层保证恢复原输入法。
"""
from __future__ import annotations

from typing import Any

from ..registry import Risk, SessionRef, Tool, ToolRegistry
from ..runctx import RunRef
from ..shots import save_screenshot
from .driver import AndroidDriver


def register_android_tools(
    registry: ToolRegistry,
    driver: AndroidDriver,
    *,
    artifacts: Any = None,
    session_ref: SessionRef | None = None,
    run_ref: RunRef | None = None,
    can_view: bool = False,
) -> None:

    def android_status(_args: dict[str, Any]) -> str:
        try:
            devices = driver.devices()
        except Exception as exc:  # noqa: BLE001 —— adb 不在 PATH 等
            return f"错误：{exc}（先安装 Android Platform-Tools 并把 adb 加入 PATH）"
        if not devices:
            return "没有检测到安卓设备（USB 调试开、授权弹窗点允许）"
        return "设备：" + ", ".join(
            f"{d['serial']}（{d['state']}）" for d in devices
        )

    def android_tree(_args: dict[str, Any]) -> str:
        try:
            return driver.refresh_tree(session_id=session_ref.get() if session_ref else None, serial=_args.get("serial"))
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    def _action(name, args):
        run = run_ref.get() if run_ref else None
        params = dict(args)
        observation = params.pop("observation", "")
        return driver.execute_action(name, observation,
                                     session_id=session_ref.get() if session_ref else None,
                                     cancel=run, **params)

    def android_tap_element(args):
        return _action("tap_element", args)

    def android_tap_coords(args):
        return _action("tap_coords", args)

    def android_swipe(args):
        return _action("swipe", args)

    def android_key(args):
        return _action("key", args)

    def android_type(args):
        return _action("type_text", args)

    def android_screenshot(_args: dict[str, Any]) -> str:
        try:
            run = run_ref.get() if run_ref else None
            if artifacts is not None:
                data, observation, _size = driver.snapshot(session_id=session_ref.get() if session_ref else None, serial=_args.get('serial'))
                return f"观察 obs={observation}（坐标动作引用本帧，单次有效）\n" + save_screenshot(
                    artifacts, data,
                    session_id=(session_ref.get() if session_ref else None),
                    run_id=(run.run_id if run else None),
                    kind="screenshot", source="android_screenshot", can_view=can_view,
                )
            path = driver.screenshot(serial=_args.get("serial"))
            return f"截图已保存：{path}"
        except Exception as exc:  # noqa: BLE001
            return f"错误：{exc}"

    registry.register(
        Tool(
            name="android_status",
            description="检查安卓设备连接状态（无设备时提示怎么连）",
            parameters={"type": "object", "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"}, }, "required": []},
            func=android_status,
            toolset="android",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="android_screen_tree",
            description=(
                "观察安卓手机当前界面：返回带 [idx] 索引的可交互元素树，"
                "第一行是观察身份 obs=<id>。点击元素时必须把 obs 原样传给"
                " android_tap_element（它靠这个判断界面有没有变）；"
                "动作后它会返回新观察，不必重复调用本工具"
            ),
            parameters={"type": "object", "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"}, }, "required": []},
            func=android_tree,
            toolset="android",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="android_screenshot",
            description="截取安卓手机当前屏幕，保存为图片文件",
            parameters={"type": "object", "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"}, }, "required": []},
            func=android_screenshot,
            toolset="android",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="android_tap_element",
            description=(
                "点击界面上第 idx 号元素（idx 和 observation 都来自 android_screen_tree 的输出）。"
                "点击前会复核界面：与观察不一致（审批等待/弹窗/跳转）就不点，"
                "返回新观察让你重新选择。改变设备状态：每轮只做一个"
            ),
            parameters={
                "type": "object",
                "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"},
                    "idx": {"type": "integer", "description": "screen_tree 里的 [idx]"},
                    "observation": {
                        "type": "string",
                        "description": "screen_tree 输出第一行的 obs=…（观察身份）",
                    },
                },
                "required": ["idx", "observation"],
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
                "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"},
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
                "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"},
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right", "back"]}
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
                "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"},
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
                "properties": {"serial": {"type":"string", "description":"多设备时指定目标序列号"}, "text": {"type": "string", "description": "要输入的文字"}},
                "required": ["text"],
            },
            func=android_type,
            toolset="android",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    # 每个设备动作都必须引用观察；不接受服务端替调用者挑选最新观察。
    for name in ("android_tap_coords", "android_swipe", "android_key", "android_type"):
        tool = registry.get(name)
        assert tool is not None
        tool.parameters["properties"]["observation"] = {"type": "string", "description": "本会话观察的 obs；坐标点击须用 screenshot 返回的 obs"}
        tool.parameters["required"].append("observation")
        tool.description += " 必须携带 observation，动作前复核，动作后返回新观察；未知结果禁止盲重试。"


    from ..result import ToolResult
    def list_apps(args):
        import json
        try:return ToolResult.ok(json.dumps(driver.list_apps(serial=args.get('serial')),ensure_ascii=False))
        except Exception as exc:return ToolResult.fail(str(exc),error_code='android_apps_unavailable')
    registry.register(Tool(name='android_list_apps',description='列出用户允许启动的应用及可核验组件；不扩大白名单。多设备需 serial。',
        parameters={'type':'object','properties':{'serial':{'type':'string'}}},func=list_apps,toolset='android',risk=Risk.SAFE,read_only=True))
    registry.register(Tool(name='android_launch_app',description='人工确认后启动用户白名单内应用，需当前观察；不接受任意 Intent 或 URL。',
        parameters={'type':'object','properties':{'serial':{'type':'string'},'package':{'type':'string'},'observation':{'type':'string'}},'required':['package','observation']},
        func=lambda args:_action('launch_app',args),toolset='android',risk=Risk.CONFIRM,requires_human=True))
    registry.register(Tool(name='android_long_press',description='人工确认后长按当前观察中的元素；观察单次有效，效果需核查。',
        parameters={'type':'object','properties':{'serial':{'type':'string'},'idx':{'type':'integer'},'observation':{'type':'string'},'duration_ms':{'type':'integer','minimum':200,'maximum':2000,'default':600}},'required':['idx','observation']},
        func=lambda args:_action('long_press',args),toolset='android',risk=Risk.CONFIRM,requires_human=True))
