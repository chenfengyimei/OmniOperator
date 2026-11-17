"""One bounded Linux desktop request; addressed writes need a fresh parent permit, with no global input or database access."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from ...domain.contracts import BusinessError
from ...domain.desktop import LINUX_ERRORS, VERSION
from ...domain.foreground import TIMEOUT_S as FOREGROUND_TIMEOUT_S
from ..platform.linux_atspi import AtspiDesktop, report
from ..platform.linux_bus import LinuxOps, RemoteError, load_types
from ..platform.linux_click_action import FocusedClick
from ..platform.linux_input import X11Input
from ..platform.linux_key_action import FocusedKey
from ..platform.linux_semantic import Semantics
from ..platform.linux_session import FactoryPort, LocalFactory, OpsPort, SessionInspector
from ..platform.linux_text import FocusedText
from ..platform.linux_window_binding import WindowBinding
from ..platform.linux_x11 import X11Desktop
from ..platform.linux_x11_wire import XError

Payload = dict[str, object]


def session_record(inspector: SessionInspector | None, error: str | None) -> Payload:
    if inspector is not None and inspector.session is not None:
        identity = (
            inspector.scope
            or "linux:logind:"
            + hashlib.sha256(json.dumps(vars(inspector.session), sort_keys=True).encode()).hexdigest()
        )
        return {"kind": "interactive", "identity": identity}
    return {"kind": "headless" if error == "desktop_headless" else "unknown", "identity": None}


def capability_report(
    inspector: SessionInspector | None,
    error: str | None,
    installed: bool,
    atspi_ready: bool,
    x11: X11Desktop | None,
    x11_ready: bool,
    x11_error: str | None,
    input_ready: bool = False,
    input_error: str | None = None,
) -> Payload:
    value = report(session_record(inspector, error), atspi_ready, error, installed)
    for item in cast(list[Payload], value["capabilities"]):
        if (
            item["id"] == "foreground_control"
            and inspector is not None
            and inspector.session is not None
            and inspector.session.kind == "x11"
        ):
            available = atspi_ready and input_ready and error is None
            item.update(
                installed=installed,
                permission="granted" if available else "unknown",
                available=available,
                error_code=None if available else input_error or error or "desktop_input_unverified",
                limitations=[
                    "人工聚焦的AT-SPI非密码编辑器可追加Unicode；明确提供原生窗口身份的节点可申请一次特殊按键，均须原生守卫核验。",
                    "每次最多128字符，原文加结果最多512字符；一次审批和逐次permit，插入后完整回读，未知结果不重放。",
                    "不获取焦点、不使用剪贴板；明确窗口身份且可核对控件命中的提供方可申请一次点击，不会猜测坐标缩放。按键或点击须逐次许可及应用状态回读，未知结果不重放。通用提供方与Wayland输入仍在开发。",
                ],
            )
        if item["id"] == "window_identity" and x11_ready:
            item.update(
                installed=True,
                permission="granted",
                available=True,
                error_code=None,
                limitations=[
                    "X11采集目标由本机服务器与XRes实际客户端PID证明归属；不使用窗口自报的PID。",
                    "语义观察使用单独的AT-SPI目标。元数据与XID不是长期对象身份证明，不授权输入或控制。",
                ],
            )
        if item["id"] == "screen_capture" and x11 is not None:
            available = x11_ready and x11.composite is not None
            item.update(
                installed=True,
                permission="granted" if available else "unknown",
                available=available,
                error_code=None if available else x11_error or x11.capture_error,
                limitations=[
                    "仅X11目标；经XRes进程核验后读取窗口已有Composite离屏图像，不读取根窗口或普通窗口帧缓冲。",
                    "无重定向图像、身份/格式无法证明时拒绝；不含鼠标与窗口管理器装饰。截图不自动附加到模型请求。",
                    "元数据不能证明跨调用的XID永久身份，也不授权动作；Wayland仍待Portal实现和实机验收。",
                ],
            )
    return value


async def perform(
    request: dict[str, Any],
    *,
    ops: OpsPort | None = None,
    factory: FactoryPort | None = None,
    x11_factory: Callable[[SessionInspector], X11Desktop] = X11Desktop,
    monitor_factory: Callable[[SessionInspector], X11Input] = X11Input,
    binding_factory: Callable[[AtspiDesktop, X11Input], WindowBinding] = WindowBinding,
    permit: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    inspector: SessionInspector | None = None
    error: str | None = None
    result: Any = None
    installed = False
    x11, x11_error, atspi_error = None, None, None
    x11_ready = atspi_ready = False
    monitor = None
    binding: WindowBinding | None = None
    input_ready, input_error = False, None
    try:
        if request["operation"] not in {"probe", "targets", "verify", "observe", "capture", "act", "foreground"}:
            raise BusinessError("desktop_observation_unavailable", "Linux write adapters are not connected")
        if (
            request["operation"] in {"act", "foreground"}
            and cast(Payload, request.get("target") or {}).get("backend") != "atspi"
        ):
            raise BusinessError("desktop_input_unverified", "Only addressed AT-SPI semantic actions are implemented")
        if ops is None:
            load_types()
            native = LinuxOps()
            ops, factory = native, LocalFactory(native)
        if factory is None:
            raise BusinessError("desktop_native_unavailable", "Linux bus factory is missing")
        installed = True
        inspector = SessionInspector(ops, factory)
        adapter = AtspiDesktop(inspector)
        async with asyncio.timeout(FOREGROUND_TIMEOUT_S - 1 if request["operation"] == "foreground" else 4):
            await inspector.open(accessibility=False)
            operation = request["operation"]
            backend = cast(Payload, request.get("target") or {}).get("backend")
            if operation in {"probe", "targets"} or backend == "atspi":
                try:
                    await inspector.open_accessibility()
                    atspi_ready = True
                except (BusinessError, RemoteError) as caught:
                    if isinstance(caught, BusinessError) and caught.code == "desktop_cleanup_pending":
                        raise
                    atspi_error = (
                        caught.code if isinstance(caught, BusinessError) else "desktop_accessibility_unavailable"
                    )
            if (
                inspector.session is not None
                and inspector.session.kind == "x11"
                and (operation in {"probe", "targets"} or backend == "x11")
            ):
                x11 = x11_factory(inspector)
                try:
                    await x11.open()
                    x11_ready = True
                except (BusinessError, RemoteError, XError, OSError) as caught:
                    x11_error = caught.code if isinstance(caught, BusinessError) else "desktop_x11_peer_unverified"
            if operation == "probe":
                if atspi_ready and inspector.session is not None and inspector.session.kind == "x11":
                    monitor = monitor_factory(inspector)
                    try:
                        await monitor.open()
                        input_ready = True
                    except (BusinessError, RemoteError, XError, OSError) as caught:
                        input_error = caught.code if isinstance(caught, BusinessError) else "desktop_input_unverified"
                result = capability_report(
                    inspector, atspi_error, installed, atspi_ready, x11, x11_ready, x11_error, input_ready, input_error
                )
            elif operation == "targets":
                result = {"items": [], "truncated": False}
                if x11_ready:
                    assert x11 is not None
                    result = await x11.targets(request["limit"])
                if atspi_ready and len(result["items"]) < request["limit"]:
                    try:
                        semantic = await adapter.targets(request["limit"] - len(result["items"]))
                        result["items"].extend(semantic["items"])
                        result["truncated"] |= semantic["truncated"]
                    except BusinessError as caught:
                        if not result["items"] or caught.code != "desktop_target_session_unverified":
                            raise
                        result["truncated"] = True
                elif atspi_ready:
                    result["truncated"] = True
                if not atspi_ready and not x11_ready:
                    raise BusinessError(
                        x11_error or atspi_error or "desktop_native_unavailable",
                        "No native discovery backend available",
                    )
            elif backend == "x11":
                if not x11_ready:
                    raise BusinessError(x11_error or "desktop_x11_session_unavailable", "X11 target unavailable")
                assert x11 is not None
                if operation == "verify":
                    result = await x11.verify(request["target"])
                elif operation == "capture":
                    result = await x11.capture(request["target"], Path(request["output_path"]))
                else:
                    raise BusinessError(
                        "desktop_observation_unavailable", "Choose an AT-SPI target for semantic observation"
                    )
            else:
                if operation == "capture":
                    raise BusinessError(
                        "desktop_capture_unavailable", "Choose an X11 target for capture; no PID-only window mapping"
                    )
                if not atspi_ready:
                    raise BusinessError(atspi_error or "desktop_accessibility_unavailable", "AT-SPI target unavailable")
                actions = False
                if operation in {"act", "foreground"} and permit is None:
                    raise BusinessError("desktop_action_revoked", "Native write requires a fresh parent permit")
                if (
                    operation in {"observe", "act", "foreground"}
                    and inspector.session is not None
                    and inspector.session.kind == "x11"
                ):
                    monitor = monitor_factory(inspector)
                    try:
                        if operation != "foreground" or request["action"]["kind"] not in {"press_key", "click"}:
                            await monitor.open()
                        actions = True
                    except (BusinessError, RemoteError, XError, OSError):
                        if operation in {"act", "foreground"}:
                            raise
                        if not await monitor.close():
                            raise BusinessError("desktop_cleanup_pending", "Input monitor cleanup is pending") from None
                        monitor = None
                if operation in {"act", "foreground"}:
                    if monitor is None or not actions:
                        raise BusinessError("desktop_input_unverified", "Reliable input-change guard is not available")
                    if operation == "foreground":
                        if request["action"]["kind"] in {"press_key", "click"}:
                            binding = binding_factory(adapter, monitor)
                            if request["action"]["kind"] == "click":
                                result = await FocusedClick(adapter, binding).click(
                                    request["target"], request["action"], cast(Callable[[], Awaitable[bool]], permit)
                                )
                            else:
                                result = await FocusedKey(adapter, binding).press(
                                    request["target"], request["action"], cast(Callable[[], Awaitable[bool]], permit)
                                )
                        else:
                            result = await FocusedText(adapter).append(
                                request["target"],
                                request["action"],
                                monitor,
                                cast(Callable[[], Awaitable[bool]], permit),
                            )
                    else:
                        result = await Semantics(adapter).act(
                            request["target"], request["action"], monitor, cast(Callable[[], Awaitable[bool]], permit)
                        )
                elif operation == "verify":
                    result = await adapter.verify(request["target"])
                else:
                    result = await adapter.observe(request["target"], actions=actions)
    except BusinessError as caught:
        error = (
            caught.code
            if caught.code
            in LINUX_ERRORS
            | {
                "desktop_native_unavailable",
                "desktop_observation_unavailable",
                "desktop_capture_unavailable",
                "desktop_capture_too_large",
            }
            else "desktop_native_unavailable"
        )
    except TimeoutError:
        error = "desktop_timeout"
    except XError:
        error = "desktop_x11_target_unverified"
    except RemoteError:
        error = (
            "desktop_session_unverified"
            if inspector is None or inspector.session is None
            else "desktop_accessibility_unavailable"
        )
    except Exception:  # noqa: BLE001 -- native/provider error text must never cross the helper boundary
        error = "desktop_native_unavailable"
    finally:
        if binding is not None:
            # Outside the business timeout: the permitted helper may still own
            # the release suffix or its original uncertain readback. Parent
            # retains this process and its slot until native cleanup completes.
            while True:
                try:
                    if await binding.close():
                        break
                except Exception:  # noqa: BLE001 -- keep the actual binding alive for cleanup, with no replay
                    error = "desktop_cleanup_pending"
                error = "desktop_cleanup_pending"
                await asyncio.sleep(0.1)
        if monitor is not None and binding is None:
            try:
                if not await monitor.close():
                    error = "desktop_cleanup_pending"
            except Exception:  # noqa: BLE001 -- a failed native guard release is never a successful action
                error = "desktop_cleanup_pending"
        if x11 is not None:
            try:
                if not await x11.close():
                    error = "desktop_cleanup_pending"
            except Exception:  # noqa: BLE001 -- preserve cleanup failure without native error text
                error = "desktop_cleanup_pending"
        if inspector is not None:
            try:
                if not await inspector.close():
                    error = "desktop_cleanup_pending"
            except Exception:  # noqa: BLE001 -- cleanup remains a visible failure, not a successful empty response
                error = "desktop_cleanup_pending"
    if request["operation"] == "probe" and error != "desktop_cleanup_pending":
        result = capability_report(
            inspector,
            error or atspi_error,
            installed,
            atspi_ready and error is None,
            x11,
            x11_ready and error is None,
            x11_error,
            input_ready and error is None,
            input_error,
        )
        error = None
    return {
        "version": VERSION,
        "request_id": request["request_id"],
        "operation": request["operation"],
        "ok": error is None,
        "result": result if error is None else None,
        "error_code": error,
    }
