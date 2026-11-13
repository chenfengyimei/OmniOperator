"""Fixed Android observation/input worker. No database, arbitrary shell or automatic device setup."""

from __future__ import annotations

import asyncio
import os
import re
import struct
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

from ...domain.android import METHODS, device, digest, input_params, package, scroll_verified
from ...domain.contracts import JSON, ActionResult, Effect, Outcome
from .android_adb import Adb
from .android_unicode import UnicodeWorker
from .protocol import MAX_FRAME, encode_response
from .protocol import request as decode_request

Payload = dict[str, object]


def hierarchy(raw: bytes, package: str) -> Payload:
    if len(raw) > 262144 or b"<!" in raw:
        raise ValueError("Unsafe or oversized hierarchy")
    start, end = raw.find(b"<?xml"), raw.rfind(b"</hierarchy>")
    if start < 0 or end < 0:
        raise ValueError("No Android hierarchy")
    xml = raw[start : end + len(b"</hierarchy>")]
    root = ET.fromstring(xml)
    if root.tag != "hierarchy":
        raise ValueError("Invalid Android hierarchy")
    nodes: list[Payload] = []
    text_bytes, truncated, seen = 0, False, 0

    def walk(element: ET.Element, path: list[int], depth: int) -> None:
        nonlocal text_bytes, truncated, seen
        seen += 1
        if seen > 4096:
            raise ValueError("Hierarchy is too large")
        if len(nodes) >= 128 or depth > 8:
            truncated = True
            return
        if element.tag == "node":
            attrs = element.attrib
            bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
            if not bounds or attrs.get("package") != package:
                return
            rect = list(map(int, bounds.groups()))
            if not (0 <= rect[0] < rect[2] <= 8192 and 0 <= rect[1] < rect[3] <= 8192):
                return
            password = attrs.get("password") == "true"
            text, description = ("", "") if password else (attrs.get("text", ""), attrs.get("content-desc", ""))
            text, description = text[:512], description[:512]
            remaining = max(0, 16384 - text_bytes)
            text = text.encode()[:remaining].decode("utf-8", errors="ignore")
            remaining -= len(text.encode())
            description = description.encode()[:remaining].decode("utf-8", errors="ignore")
            text_bytes += len((text + description).encode())
            if text_bytes >= 16384:
                truncated = True
            actions: list[str] = []
            if not password and attrs.get("enabled") == "true":
                actions.append("launch")
                if attrs.get("focused") == "true":
                    actions.append("key")
                if attrs.get("long-clickable") == "true":
                    actions.append("long_press")
                if attrs.get("scrollable") == "true":
                    actions.append("swipe")
                if attrs.get("clickable") == "true":
                    actions.append("tap")
                    if attrs.get("checkable") == "true":
                        actions.append("toggle")
                if attrs.get("focused") == "true" and attrs.get("class", "").endswith("EditText"):
                    actions.extend(("append_ascii", "append_unicode"))
            nodes.append(
                {
                    "path": path,
                    "proof": digest(dict(attrs)),
                    "text": text,
                    "description": description,
                    "resource_id": attrs.get("resource-id", "")[:256],
                    "class": attrs.get("class", "")[:128],
                    "bounds": rect,
                    "password": password,
                    "actions": actions,
                    "checked": attrs.get("checked") == "true",
                }
            )
        for index, child in enumerate(element):
            walk(child, [*path, index], depth + 1)

    walk(root, [], 0)
    # Keep a response below the fixed 64 KiB IPC frame even with worst-case UTF-8 metadata.
    import json

    while nodes and len(json.dumps(nodes, ensure_ascii=False).encode()) > 56000:
        nodes.pop()
        truncated = True
    return {"tree_sha256": digest(xml.decode("utf-8")), "nodes": nodes, "truncated": truncated}


class AndroidWorker:
    def __init__(self) -> None:
        self.ready: Payload | None = None
        self.unicode = UnicodeWorker()

    async def observe(self, adb: Adb, target: Payload, allowed_packages: list[str] | None = None) -> Payload:
        await adb.require(target)
        before = await adb.window(target)
        if allowed_packages is not None and before["package"] not in allowed_packages:
            raise ValueError("Foreground application is outside the approved scope")
        # shell-v2 raw has no controlling TTY. Dump to this process's stdout without a remote temporary file.
        raw = await adb.shell(cast(int, target["transport_id"]), "uiautomator dump /proc/self/fd/1")
        tree = hierarchy(raw, cast(str, before["package"]))
        after = await adb.window(target)
        await adb.require(target)
        if before != after:
            raise ValueError("Android foreground changed")
        return {"device": target, "window": after, **tree}

    async def invoke(self, name: str, args: Payload, context: Payload) -> ActionResult:
        if name.startswith("android_unicode_"):
            return await self.unicode.invoke(name, args, context)
        attempted = False
        try:
            connection = cast(Payload, args["connection"])
            adb = Adb(cast(int, connection["port"]), cast(str, connection["serial"]))
            async with asyncio.timeout(20):
                if name == "android_probe":
                    return ActionResult(
                        Outcome.OK,
                        "Android device identity observed",
                        data=cast(dict[str, JSON], {"device": await adb.identify()}),
                    )
                target = device(args["device"])
                transport = cast(int, target["transport_id"])
                allowed_raw = args.get("allowed_packages")
                allowed: list[str] | None = None
                if allowed_raw is not None:
                    if not isinstance(allowed_raw, list):
                        raise ValueError("Invalid application scope")
                    scope = cast(list[object], allowed_raw)
                    if not 1 <= len(scope) <= 16:
                        raise ValueError("Invalid application scope")
                    allowed = [package(p) for p in scope]
                if name == "android_observe":
                    return ActionResult(
                        Outcome.OK,
                        "Android UI observed",
                        data=cast(dict[str, JSON], await self.observe(adb, target, allowed)),
                    )
                if name == "android_apps":
                    await adb.require(target)
                    raw = (await adb.shell(transport, "pm list packages --user " + str(target["user_id"]))).decode(
                        "utf-8"
                    )
                    values: list[str] = []
                    for line in raw.splitlines():
                        if not line.startswith("package:"):
                            raise ValueError("Invalid package inventory")
                        values.append(package(line.removeprefix("package:")))
                        if len(values) > 4096:
                            raise ValueError("Package inventory exceeds limit")
                    await adb.require(target)
                    after = cast(str, args.get("after", ""))
                    if after:
                        package(after)
                    values = sorted({v for v in values if (allowed is None or v in allowed) and v > after})
                    return ActionResult(
                        Outcome.OK,
                        "Installed applications queried",
                        data=cast(
                            dict[str, JSON],
                            {
                                "items": values[:200],
                                "truncated": len(values) > 200,
                                "next_after": values[199] if len(values) > 200 else None,
                            },
                        ),
                    )
                if name == "android_capture":
                    await adb.require(target)
                    before = await adb.window(target)
                    raw = await adb.shell(transport, "screencap -p", maximum=16 * 1024 * 1024)
                    await adb.require(target)
                    if before != await adb.window(target) or not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) < 33:
                        raise ValueError("Invalid or changed Android capture")
                    width, height = struct.unpack(">II", raw[16:24])
                    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 16000000:
                        raise ValueError("Capture dimensions exceed limit")
                    destination = Path(cast(str, args["destination"]))
                    if not destination.is_absolute() or not destination.parent.is_dir():
                        raise ValueError("Capture destination is unverified")
                    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    return ActionResult(
                        Outcome.OK, "Android capture staged", data={"bytes": len(raw), "width": width, "height": height}
                    )
                if name == "android_ready":
                    observed = await self.observe(adb, target, allowed)
                    node = cast(Payload, args["node"])
                    kind = cast(str, args["kind"])
                    params = input_params(kind, args.get("params", {}))
                    observed_nodes = cast(list[Payload], observed["nodes"])
                    observed_window = cast(Payload, observed["window"])
                    actual = next((n for n in observed_nodes if n["path"] == node["path"]), None)
                    if (
                        observed["tree_sha256"] != args["tree_sha256"]
                        or observed["window"] != args["window"]
                        or not observed_window["unlocked"]
                        or actual != node
                        or kind not in cast(list[object], node["actions"])
                    ):
                        raise ValueError("Android observation changed or input is unavailable")
                    component: str | None = None
                    if kind == "launch":
                        launch_target = cast(str, params["package"])
                        if allowed is not None and launch_target not in allowed:
                            raise ValueError("Launch destination is outside the approved scope")
                        raw = (
                            await adb.shell(
                                transport,
                                f"cmd package resolve-activity --brief --user {target['user_id']} -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p {launch_target}",
                                maximum=8192,
                            )
                        ).decode("utf-8")
                        matches = [
                            line.strip()
                            for line in raw.splitlines()
                            if re.fullmatch(r"[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+", line.strip())
                        ]
                        if len(matches) != 1 or matches[0].split("/", 1)[0] != launch_target:
                            raise ValueError("Launcher activity is absent or ambiguous")
                        component = matches[0]
                    token = str(uuid.uuid4())
                    self.ready = {
                        "token": token,
                        "context": context,
                        "args": args,
                        "deadline": asyncio.get_running_loop().time() + 5,
                        "component": component,
                    }
                    return ActionResult(
                        Outcome.OK, "Android input ready; host permission required", data={"token": token}
                    )
                if name != "android_commit":
                    raise ValueError("Unknown Android operation")
                bound, self.ready = self.ready, None
                if (
                    not bound
                    or args.get("token") != bound["token"]
                    or context != bound["context"]
                    or asyncio.get_running_loop().time() > cast(float, bound["deadline"])
                ):
                    raise ValueError("Android permit expired")
                action = cast(Payload, bound["args"])
                if args["device"] != action["device"] or args["connection"] != action["connection"]:
                    raise ValueError("Android permit changed")
                fresh = await self.observe(adb, target, cast("list[str] | None", action.get("allowed_packages")))
                node = cast(Payload, action["node"])
                action_kind = cast(str, action["kind"])
                params = input_params(action_kind, action.get("params", {}))
                fresh_window = cast(Payload, fresh["window"])
                if (
                    fresh["tree_sha256"] != action["tree_sha256"]
                    or fresh["window"] != action["window"]
                    or not fresh_window["unlocked"]
                ):
                    raise ValueError("Android target changed before input")
                if action_kind == "append_ascii":
                    text = action["text"]
                    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9 .,_@+-]{1,256}", text):
                        raise ValueError("Unsupported Android text")
                    command = "input text '" + text.replace(" ", "%s") + "'"
                elif action_kind == "key":
                    command = "input keyevent KEYCODE_" + cast(str, params["key"])
                elif action_kind == "launch":
                    command = f"am start -W --user {target['user_id']} -n '{bound['component']}' -a android.intent.action.MAIN -c android.intent.category.LAUNCHER"
                else:
                    x1, y1, x2, y2 = cast(list[int], node["bounds"])
                    x, y = (x1 + x2) // 2, (y1 + y2) // 2
                    if action_kind == "long_press":
                        command = f"input swipe {x} {y} {x} {y} {params['duration_ms']}"
                    elif action_kind == "swipe":
                        dx, dy = max(1, (x2 - x1) * 3 // 10), max(1, (y2 - y1) * 3 // 10)
                        paths = {
                            "up": (x, y + dy, x, y - dy),
                            "down": (x, y - dy, x, y + dy),
                            "left": (x + dx, y, x - dx, y),
                            "right": (x - dx, y, x + dx, y),
                        }
                        points = paths[cast(str, params["direction"])]
                        if not (
                            x1 <= points[0] < x2
                            and x1 <= points[2] < x2
                            and y1 <= points[1] < y2
                            and y1 <= points[3] < y2
                        ):
                            raise ValueError("Scrollable control has insufficient geometry")
                        command = "input swipe " + " ".join(map(str, points)) + " " + str(params["duration_ms"])
                    else:
                        command = f"input tap {x} {y}"

                def dispatched() -> None:
                    nonlocal attempted
                    attempted = True

                await adb.shell(transport, command, maximum=4096, before_send=dispatched)
                if action_kind == "launch":
                    await adb.require(target)
                    window = await adb.window(target)
                    dest, activity = cast(str, bound["component"]).split("/", 1)
                    window_activity = cast(str, window["activity"])
                    expected_activity = dest + activity if activity.startswith(".") else activity
                    actual_activity = dest + window_activity if window_activity.startswith(".") else window_activity
                    if window["unlocked"] and window["package"] == dest and actual_activity == expected_activity:
                        return ActionResult(
                            Outcome.OK,
                            "Requested application is in the foreground",
                            Effect.APPLIED,
                            verification="foreground_application",
                        )
                    raise ValueError("Launch foreground could not be verified")
                verified = await self.observe(adb, target, cast("list[str] | None", action.get("allowed_packages")))
                verified_nodes = cast(list[Payload], verified["nodes"])
                matches = [
                    n
                    for n in verified_nodes
                    if n["path"] == node["path"]
                    and n["resource_id"] == node["resource_id"]
                    and n["class"] == node["class"]
                ]
                if verified["window"] == action["window"] and len(matches) == 1:
                    after = matches[0]
                    if (
                        action_kind == "swipe"
                        and after["bounds"] == node["bounds"]
                        and scroll_verified(
                            cast(list[Payload], fresh["nodes"]), verified_nodes, node, cast(str, params["direction"])
                        )
                    ):
                        return ActionResult(
                            Outcome.OK,
                            "Stable scroll content moved along the approved direction",
                            Effect.APPLIED,
                            verification="scroll_content_moved",
                        )
                    confirmed = (action_kind == "toggle" and after["checked"] != node["checked"]) or (
                        action_kind == "append_ascii"
                        and after["text"] == cast(str, node["text"]) + cast(str, action["text"])
                    )
                    if confirmed:
                        return ActionResult(
                            Outcome.OK,
                            "Android postcondition verified",
                            Effect.APPLIED,
                            verification="checked_changed" if action_kind == "toggle" else "text_appended",
                        )
                # Generic coordinate taps have no trustworthy semantic postcondition.
                return ActionResult(
                    Outcome.UNKNOWN,
                    "Android input dispatched; outcome requires review",
                    Effect.UNKNOWN,
                    error_code="android_input_unverified",
                )
        except Exception:  # noqa: BLE001 -- device UI and native exceptions must never reach diagnostics
            return (
                ActionResult(
                    Outcome.UNKNOWN,
                    "Android input outcome requires review",
                    Effect.UNKNOWN,
                    error_code="android_input_unknown",
                )
                if attempted
                else ActionResult(
                    Outcome.DENIED,
                    "Android device or observation is unavailable",
                    error_code="android_target_unavailable",
                )
            )


async def serve() -> None:
    worker = AndroidWorker()
    try:
        while raw := await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1):
            request = decode_request(raw)
            method = cast(str, request["method"])
            if method not in METHODS or request["writes"] is not METHODS[method]:
                return
            result = await worker.invoke(method, cast(Payload, request["arguments"]), cast(Payload, request["context"]))
            sys.stdout.buffer.write(encode_response(cast(str, request["id"]), result))
            sys.stdout.buffer.flush()
    finally:
        await worker.unicode.close()


if __name__ == "__main__":
    asyncio.run(serve())
