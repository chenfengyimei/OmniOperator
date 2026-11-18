"""Read-only AT-SPI windows in a proven logind session. No X11/Portal capture or input fallback."""

from __future__ import annotations

from typing import Any, cast

from omni.adapters.platform.desktop.linux.linux_bus import Process, failure
from omni.adapters.platform.desktop.linux.linux_session import (
    ACCESSIBLE,
    PROPERTIES,
    ROOT,
    SessionInspector,
    number,
    object_path,
    one,
    text,
    unique,
    variant,
)
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, validate_observation, validate_target, validate_targets

Payload = dict[str, object]

COMPONENT = "org.a11y.atspi.Component"
TEXT = "org.a11y.atspi.Text"
WINDOW_ROLES = {16, 23, 69}
PASSWORD_ROLE = 40
MAX_APPS, MAX_ENUMERATED = 32, 128
MAX_NODES, MAX_DEPTH, MAX_TEXT = 128, 8, 16384


def reference(value: object) -> tuple[str, str]:
    if not isinstance(value, list) or len(cast(list[object], value)) != 2:
        raise failure("desktop_bus_response_invalid")
    return unique(cast(list[object], value)[0]), object_path(cast(list[object], value)[1])


def native_id(name: str, path: str) -> str:
    value = "atspi:" + unique(name) + "|" + object_path(path)
    if len(value) > 512:
        raise failure("desktop_target_unverified")
    return value


def parse_native_id(value: object) -> tuple[str, str]:
    value = text(value)
    if not value.startswith("atspi:") or value.count("|") != 1:
        raise failure("desktop_target_unverified")
    name, path = value[6:].split("|")
    return unique(name), object_path(path)


def state(values: object) -> set[int]:
    if not isinstance(values, list) or not 1 <= len(cast(list[object], values)) <= 4:
        raise failure("desktop_bus_response_invalid")
    return {
        index * 32 + bit
        for index, value in enumerate(cast(list[object], values))
        for bit in range(32)
        if number(value) & (1 << bit)
    }


def clean(value: object) -> str:
    if not isinstance(value, str):
        raise failure("desktop_bus_response_invalid")
    return "".join(c for c in value if (ord(c) >= 32 or c in "\t\r\n") and not 127 <= ord(c) <= 159)[:512]


def report(session: Payload, available: bool, error: str | None, installed: bool) -> Payload:
    capabilities: list[Payload] = []
    for identity in ("window_identity", "accessibility", "screen_capture", "foreground_control"):
        implemented = identity in {"window_identity", "accessibility"} and installed
        ready = implemented and available
        capabilities.append(
            {
                "id": identity,
                "supported": True,
                "installed": implemented,
                "permission": "granted" if ready else "unknown",
                "available": ready,
                "error_code": None
                if ready
                else error
                if identity in {"window_identity", "accessibility"}
                else "desktop_adapter_not_implemented",
                "limitations": [
                    "仅列出AT-SPI总线中可证明属于当前活动图形会话的同用户窗口；用户服务应用不能证明会话时拒绝。",
                    "边界相对所选窗口，不用于全局坐标输入。仅X11输入监测可核验时提供逐次审批的语义动作候选；密码及其子树不读取。",
                    "X11采集另行核验服务器与目标，Wayland采集使用显式Portal授权。通用前台输入尚未开放；环境变量或同UID不代表控制权限。",
                ],
                "recovery": []
                if ready
                else ["检查本机logind活动会话、辅助功能服务和已锁定的desktop-linux依赖；无桌面环境可正常运行核心。"],
            }
        )
    return {
        "version": VERSION,
        "platform": "linux",
        "session": session,
        "capabilities": capabilities,
        "acceptance": "pending",
    }


class AtspiDesktop:
    def __init__(self, inspector: SessionInspector) -> None:
        self.inspector = inspector

    async def setup(self) -> None:
        await self.inspector.open()

    async def call(
        self,
        ref: tuple[str, str],
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        *,
        returns: str,
    ) -> Any:
        assert self.inspector.accessibility is not None
        return one(
            await self.inspector.accessibility.call(ref[0], ref[1], interface, member, signature, body, returns=returns)
        )

    async def property(self, ref: tuple[str, str], interface: str, name: str, signature: str) -> Any:
        return variant(await self.call(ref, PROPERTIES, "Get", "ss", [interface, name], returns="v"), signature)

    async def children_count(self, ref: tuple[str, str]) -> int:
        return number(await self.property(ref, ACCESSIBLE, "ChildCount", "i"), 2147483647)

    async def child(self, ref: tuple[str, str], index: int) -> tuple[str, str]:
        return reference(await self.call(ref, ACCESSIBLE, "GetChildAtIndex", "i", [index], returns="(so)"))

    async def role_state(self, ref: tuple[str, str]) -> tuple[int, set[int]]:
        role = number(await self.call(ref, ACCESSIBLE, "GetRole", returns="u"), 65535)
        flags = state(await self.call(ref, ACCESSIBLE, "GetState", returns="au"))
        if 6 in flags:
            raise failure("desktop_target_changed")
        return role, flags

    async def target(self, ref: tuple[str, str], process: Process | None = None) -> Payload:
        process = process or await self.inspector.app(ref[0])
        role, flags = await self.role_state(ref)
        if role not in WINDOW_ROLES or not {25, 30} <= flags:
            raise failure("desktop_target_unverified")
        application = reference(await self.call(ref, ACCESSIBLE, "GetApplication", returns="(so)"))
        parent = reference(await self.property(ref, ACCESSIBLE, "Parent", "(so)"))
        if application != parent or application[0] != ref[0]:
            raise failure("desktop_target_unverified")
        assert self.inspector.scope is not None
        return validate_target(
            {
                "platform": "linux",
                "backend": "atspi",
                "native_id": native_id(*ref),
                "pid": process.pid,
                "process_created": process.created,
                "application_id": process.executable,
                "session_id": self.inspector.scope,
            }
        )

    async def targets(self, limit: int) -> Payload:
        await self.inspector.check()
        assert self.inspector.registry is not None
        root = (self.inspector.registry.name, ROOT)
        applications = await self.children_count(root)
        items: list[Payload]
        seen: set[str]
        items, seen = [], set()
        truncated = applications > MAX_APPS
        enumerated = eligible = unverified = 0
        for index in range(min(applications, MAX_APPS)):
            app = await self.child(root, index)
            try:
                process = await self.inspector.app(app[0])
            except BusinessError as error:
                if error.code not in {"desktop_target_session_unverified", "desktop_target_changed"}:
                    raise
                unverified += 1
                truncated = True
                continue
            eligible += 1
            count = await self.children_count(app)
            for child_index in range(min(count, MAX_ENUMERATED - enumerated)):
                enumerated += 1
                ref = await self.child(app, child_index)
                if ref[0] != app[0]:
                    truncated = True
                    continue
                role, flags = await self.role_state(ref)
                if role not in WINDOW_ROLES or not {25, 30} <= flags:
                    continue
                target = await self.target(ref, process)
                if target["native_id"] in seen:
                    raise failure("desktop_bus_response_invalid")
                seen.add(cast(str, target["native_id"]))
                if len(items) >= limit:
                    truncated = True
                    break
                items.append(target)
            if count > MAX_ENUMERATED or enumerated >= MAX_ENUMERATED or len(items) >= limit:
                truncated = True
                break
        if not eligible and unverified:
            raise failure("desktop_target_session_unverified")
        for item in items:
            if await self.target(parse_native_id(item["native_id"])) != item:
                raise failure("desktop_target_changed")
        await self.inspector.check()
        return validate_targets({"items": items, "truncated": truncated}, limit)

    async def verified(self, target: Payload) -> tuple[str, str]:
        await self.inspector.check()
        if (
            target["platform"] != "linux"
            or target["backend"] != "atspi"
            or target["session_id"] != self.inspector.scope
        ):
            raise failure("desktop_target_changed")
        ref = parse_native_id(target["native_id"])
        if await self.target(ref) != target:
            raise failure("desktop_target_changed")
        return ref

    async def verify(self, target: Payload) -> Payload:
        try:
            await self.verified(target)
        except BusinessError as error:
            if error.code not in {
                "desktop_target_changed",
                "desktop_target_unverified",
                "desktop_target_session_unverified",
            }:
                raise
            return {"matches": False, "target": None, "reason": error.code}
        await self.inspector.check()
        return {"matches": True, "target": target, "reason": None}

    async def observe(self, target: Payload, *, actions: bool = False) -> Payload:
        from omni.adapters.platform.desktop.linux.linux_semantic import Semantics, digest

        semantics = Semantics(self) if actions else None
        ref = await self.verified(target)
        nodes: list[Payload]
        seen: set[tuple[str, str]]
        nodes, seen = [], set()
        remaining, truncated = MAX_TEXT, False

        def budget(value: object) -> str:
            nonlocal remaining, truncated
            if isinstance(value, str) and len(value) > 512:
                truncated = True
            value = clean(value)
            while len(value.encode("utf-8")) > remaining:
                value = value[:-1]
                truncated = True
            remaining -= len(value.encode("utf-8"))
            return value

        async def visit(
            current: tuple[str, str],
            parent: int | None,
            depth: int,
            parent_ref: tuple[str, str] | None = None,
            path: list[int] | None = None,
            trail: tuple[tuple[list[str], int, str], ...] = (),
        ) -> None:
            nonlocal truncated
            if len(nodes) >= MAX_NODES or current in seen:
                truncated = True
                return
            if current[0] != ref[0]:
                truncated = True
                return
            seen.add(current)
            role, flags = await self.role_state(current)
            password = role == PASSWORD_ROLE
            raw_name = "" if password else await self.property(current, ACCESSIBLE, "Name", "s")
            name = "" if password else budget(raw_name)
            content, bounds = "", [0, 0, 0, 0]
            interfaces: list[str]
            raw_text, count, interfaces = "", 0, []
            if not password:
                interfaces = await self.call(current, ACCESSIBLE, "GetInterfaces", returns="as")
                if (
                    not isinstance(cast(object, interfaces), list)
                    or len(interfaces) > 64
                    or any(not isinstance(cast(object, i), str) or len(i) > 255 for i in interfaces)
                ):
                    raise failure("desktop_bus_response_invalid")
                if TEXT in interfaces:
                    count = number(await self.property(current, TEXT, "CharacterCount", "i"), 2147483647)
                    truncated = truncated or count > 512
                    raw_text = await self.call(current, TEXT, "GetText", "ii", [0, min(count, 512)], returns="s")
                    content = budget(raw_text)
                if COMPONENT in interfaces:
                    # CoordType.WINDOW: never fabricate global coordinates on Wayland.
                    bounds = await self.call(current, COMPONENT, "GetExtents", "u", [1], returns="(iiii)")
                    if (
                        not isinstance(cast(object, bounds), list)
                        or len(bounds) != 4
                        or any(type(v) is not int or abs(v) > 10000000 for v in bounds)
                        or bounds[2] < 0
                        or bounds[3] < 0
                    ):
                        raise failure("desktop_bus_response_invalid")
            identity = len(nodes) + 1
            path = [] if path is None else path
            chain = (*trail, (list(current), role, digest(raw_name)))
            nodes.append(
                {
                    "id": identity,
                    "parent": parent,
                    "role": {16: "atspi-dialog", 23: "atspi-frame", 69: "atspi-window", 40: "atspi-password-text"}.get(
                        role, "atspi-role-" + str(role)
                    ),
                    "name": name,
                    "text": content,
                    "password": password,
                    "enabled": {8, 24} <= flags,
                    "offscreen": not {25, 30} <= flags,
                    "bounds": bounds,
                }
            )
            if semantics is not None:
                proof, kinds = None, []
                if (
                    not password
                    and count <= 512
                    and name == raw_name
                    and content == raw_text
                    and all(i <= 127 for i in path)
                ):
                    proof, kinds = await semantics.observed(
                        current,
                        parent_ref,
                        path,
                        chain,
                        {"role": role, "flags": flags, "name": raw_name, "text": raw_text, "interfaces": interfaces},
                        ref,
                    )
                nodes[-1].update(reference=proof, actions=kinds)
            if password:
                return
            count = await self.children_count(current)
            if depth >= MAX_DEPTH:
                truncated = truncated or count > 0
                return
            length = min(count, MAX_NODES - len(nodes))
            truncated = truncated or count > length
            for index in range(length):
                await visit(await self.child(current, index), identity, depth + 1, current, [*path, index], chain)

        await visit(ref, None, 1)
        await self.verified(target)
        return validate_observation(
            {"target": target, "nodes": nodes, "truncated": truncated, "coordinate_space": "window"}, target
        )

    async def close(self) -> bool:
        return await self.inspector.close()
