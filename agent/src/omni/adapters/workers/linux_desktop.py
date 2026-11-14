"""One Linux read-only request in the owned helper. No service activation, settings, input or database access."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from ...domain.contracts import BusinessError
from ...domain.desktop import LINUX_ERRORS, VERSION
from ..platform.linux_atspi import AtspiDesktop, report
from ..platform.linux_bus import LinuxOps, RemoteError, load_types
from ..platform.linux_input import X11Input
from ..platform.linux_semantic import Semantics
from ..platform.linux_session import FactoryPort, LocalFactory, OpsPort, SessionInspector
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
) -> Payload:
    value = report(session_record(inspector, error), atspi_ready, error, installed)
    for item in cast(list[Payload], value["capabilities"]):
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
    permit: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    inspector: SessionInspector | None = None
    error: str | None = None
    result: Any = None
    installed = False
    x11, x11_error, atspi_error = None, None, None
    x11_ready = atspi_ready = False
    monitor = None
    try:
        if request["operation"] not in {"probe", "targets", "verify", "observe", "capture", "act"}:
            raise BusinessError("desktop_observation_unavailable", "Linux write adapters are not connected")
        if request["operation"] == "act" and cast(Payload, request.get("target") or {}).get("backend") != "atspi":
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
        async with asyncio.timeout(4):
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
                result = capability_report(inspector, atspi_error, installed, atspi_ready, x11, x11_ready, x11_error)
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
                if operation == "act" and permit is None:
                    raise BusinessError("desktop_action_revoked", "Native write requires a fresh parent permit")
                if (
                    operation in {"observe", "act"}
                    and inspector.session is not None
                    and inspector.session.kind == "x11"
                ):
                    monitor = monitor_factory(inspector)
                    try:
                        await monitor.open()
                        actions = True
                    except (BusinessError, RemoteError, XError, OSError):
                        if operation == "act":
                            raise
                        if not await monitor.close():
                            raise BusinessError("desktop_cleanup_pending", "Input monitor cleanup is pending") from None
                        monitor = None
                if operation == "act":
                    if monitor is None or not actions:
                        raise BusinessError("desktop_input_unverified", "Reliable input-change guard is not available")
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
        if monitor is not None:
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
