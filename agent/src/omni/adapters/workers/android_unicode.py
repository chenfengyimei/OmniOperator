"""Fixed IME transaction and authenticated localabstract IPC; no database or arbitrary shell."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import uuid

from ...domain.android import device, identity
from ...domain.android_unicode import (
    COMPONENT,
    MAX_PACKET,
    PACKAGE,
    SOCKET,
    fingerprint,
    ime_component,
    key_value,
    packet,
    text_value,
    unpack,
)
from ...domain.contracts import ActionResult, Effect, Outcome
from .android_adb import Adb


class UnicodeWorker:
    def __init__(self):
        self.snapshot = self.args = self.context = None
        self.reader = self.writer = self.key = self.token = None
        self.begun = False

    async def environment(self, adb: Adb, target: dict) -> dict:
        await adb.require(target)

        async def setting(name):
            raw = await adb.shell(
                target["transport_id"], f"settings --user {target['user_id']} get secure {name}", maximum=32768
            )
            return raw.decode("utf-8").strip()

        current = ime_component(await setting("default_input_method"))
        enabled = await setting("enabled_input_methods")
        ids = [ime_component(v.split(";", 1)[0]) for v in enabled.split(":") if v]
        subtype = await setting("selected_input_method_subtype")
        if (
            not re.fullmatch(r"-?[0-9]{1,10}", subtype)
            or not -2147483648 <= int(subtype) <= 2147483647
            or len(ids) > 64
            or current not in ids
        ):
            raise ValueError("IME settings are unverified")
        await adb.require(target)
        return {
            "current_ime": current,
            "current_subtype": int(subtype),
            "enabled_sha256": hashlib.sha256(enabled.encode()).hexdigest(),
            "result_code": "observed",
            "enabled_ids": ids,
        }

    async def call(self, method: str, data: dict) -> dict:
        if self.writer is None or self.reader is None or self.key is None or self.snapshot is None or self.args is None:
            raise ValueError("Helper connection is absent")
        nonce = secrets.token_hex(16)
        request = {
            "version": 1,
            "nonce": nonce,
            "method": method,
            "binding": fingerprint(
                {"context": self.context, "nonce": self.snapshot["prepare_nonce"], "action_id": self.args["action_id"]}
            ),
            "data": data,
        }
        async with asyncio.timeout(3):
            self.writer.write(packet(request, self.key))
            await self.writer.drain()
            length = int.from_bytes(await self.reader.readexactly(4), "big")
            if not 1 <= length <= MAX_PACKET:
                raise ValueError("Helper response exceeds limit")
            result = unpack(await self.reader.readexactly(length), await self.reader.readexactly(32), self.key, nonce)
        if result.get("status") not in {"denied", "unknown", "closing"} and (
            result.get("certificate_sha256") != self.args["unicode_profile"]["profile"]["certificate_sha256"]
            or type(result.get("user")) is not int
            or result["user"] != self.args["device"]["user_id"]
        ):
            raise ValueError("Helper identity changed")
        return result

    async def close(self):
        if self.writer is not None:
            self.writer.close()
            try:
                async with asyncio.timeout(1):
                    await self.writer.wait_closed()
            except (OSError, TimeoutError):
                pass
        self.reader = self.writer = self.key = self.token = None

    async def invoke(self, name: str, args: dict, context: dict) -> ActionResult:
        attempted = False
        try:
            adb = Adb(args["connection"]["port"], args["connection"]["serial"])
            target = device(args["device"])
            if name == "android_unicode_prepare":
                if self.snapshot is not None:
                    raise ValueError("IME preparation cannot be reused")
                await adb.require(target)
                meta = args["unicode_profile"]["profile"]
                paths = (
                    (
                        await adb.shell(
                            target["transport_id"], f"pm path --user {target['user_id']} {PACKAGE}", maximum=8192
                        )
                    )
                    .decode()
                    .splitlines()
                )
                if len(paths) != 1 or not paths[0].startswith("package:"):
                    raise ValueError("Pinned helper APK is unavailable")
                path = paths[0].removeprefix("package:")
                if not re.fullmatch(r"/[A-Za-z0-9_./=+~-]+\.apk", path):
                    raise ValueError("Invalid helper APK path")
                checksum = (
                    (await adb.shell(target["transport_id"], "sha256sum '" + path + "'", maximum=8192)).decode().split()
                )
                if not checksum or checksum[0] != meta["apk_sha256"]:
                    raise ValueError("Installed helper APK fingerprint changed")
                state = await self.environment(adb, target)
                if COMPONENT not in state["enabled_ids"]:
                    raise ValueError("Enable the helper explicitly in Android settings first")
                self.snapshot = {
                    "prepare_nonce": str(uuid.uuid4()),
                    "original_ime": state["current_ime"],
                    "original_subtype": state["current_subtype"],
                    "enabled_sha256": state["enabled_sha256"],
                }
                self.args, self.context = dict(args), dict(context)
                return ActionResult(Outcome.OK, "IME restoration snapshot prepared", data=dict(self.snapshot))
            if name in {"android_unicode_restore", "android_unicode_environment"}:
                snapshot = args["environment"]
                ime_component(snapshot["original_ime"])
                await self.close()
                state = await self.environment(adb, target)
                if (
                    name == "android_unicode_restore"
                    and state["current_ime"] == COMPONENT
                    and snapshot["original_ime"] != COMPONENT
                ):
                    # Give the paired helper's public-API subtype restoration a bounded chance to finish.
                    for _ in range(3):
                        await asyncio.sleep(0.1)
                        state = await self.environment(adb, target)
                        if state["current_ime"] != COMPONENT:
                            break
                    if (
                        state["current_ime"] == COMPONENT
                        and state["enabled_sha256"] == snapshot["enabled_sha256"]
                        and snapshot["original_ime"] in state["enabled_ids"]
                    ):

                        def dispatched():
                            nonlocal attempted
                            attempted = True

                        await adb.shell(
                            target["transport_id"],
                            f"ime set --user {target['user_id']} '{snapshot['original_ime']}'",
                            maximum=8192,
                            before_send=dispatched,
                        )
                        state = await self.environment(adb, target)
                restored = (
                    state["current_ime"] == snapshot["original_ime"]
                    and state["current_subtype"] == snapshot["original_subtype"]
                    and state["enabled_sha256"] == snapshot["enabled_sha256"]
                )
                state["result_code"] = "restored" if restored else "environment_changed"
                state.pop("enabled_ids")
                if name == "android_unicode_environment":
                    return ActionResult(Outcome.OK, "IME environment observed", data=state)
                return ActionResult(
                    Outcome.OK if restored else Outcome.UNKNOWN,
                    "IME restoration verified" if restored else "IME restoration requires owner review",
                    Effect.APPLIED if restored and attempted else Effect.NONE if restored else Effect.UNKNOWN,
                    verification="ime_restored" if restored else "",
                    error_code="" if restored else "android_ime_cleanup_pending",
                    data=state,
                )
            if (
                self.snapshot is None
                or self.args is None
                or context != self.context
                or args.get("prepare_nonce") != self.snapshot["prepare_nonce"]
                or args["device"] != self.args["device"]
                or args["connection"] != self.args["connection"]
            ):
                raise ValueError("IME transaction binding changed")
            await adb.require(target)
            if name == "android_unicode_begin":
                current = await self.environment(adb, target)
                if (
                    self.begun
                    or current["current_ime"] != self.snapshot["original_ime"]
                    or current["current_subtype"] != self.snapshot["original_subtype"]
                    or current["enabled_sha256"] != self.snapshot["enabled_sha256"]
                ):
                    raise ValueError("IME settings changed before selection")
                if current["current_ime"] != COMPONENT:

                    def dispatched():
                        nonlocal attempted
                        attempted = True

                    await adb.shell(
                        target["transport_id"],
                        f"ime set --user {target['user_id']} {COMPONENT}",
                        maximum=8192,
                        before_send=dispatched,
                    )
                selected = await self.environment(adb, target)
                if (
                    selected["current_ime"] != COMPONENT
                    or selected["enabled_sha256"] != self.snapshot["enabled_sha256"]
                ):
                    raise ValueError("Temporary helper IME was not selected")
                self.begun = True
                return ActionResult(
                    Outcome.OK, "Temporary helper IME selected", Effect.APPLIED if attempted else Effect.NONE
                )
            window = await adb.window(target)
            if not self.begun or not window["unlocked"] or window["package"] != self.args["window"]["package"]:
                raise ValueError("Editor foreground changed")
            if name == "android_unicode_ready":
                self.key = key_value(args["pairing_key"])
                async with asyncio.timeout(3):
                    self.reader, self.writer = await asyncio.open_connection("127.0.0.1", adb.port)
                    await adb.request(self.reader, self.writer, "host:transport-id:" + str(target["transport_id"]))
                    await adb.request(self.reader, self.writer, "localabstract:" + SOCKET + str(target["user_id"]))
                reply = await self.call(
                    "prepare",
                    {
                        "application": self.args["window"]["package"],
                        "resource": self.args["node"]["resource_id"],
                        "before_hash": hashlib.sha256(self.args["node"]["text"].encode()).hexdigest(),
                        "user": target["user_id"],
                        "original_ime": self.snapshot["original_ime"],
                        "original_subtype": self.snapshot["original_subtype"],
                    },
                )
                if reply.get("status") != "ready":
                    raise ValueError("Paired helper refused the editor")
                self.token = identity(reply.get("token"))
                return ActionResult(Outcome.OK, "Paired editor ready; host permission required")
            if name == "android_unicode_commit":
                if args.get("text") != self.args["text"] or args.get("params") != self.args["params"]:
                    raise ValueError("Approved Unicode input changed")
                text = text_value(self.args["text"])
                if self.token is None:
                    raise ValueError("Editor permit is absent")
                token, self.token = self.token, None
                attempted = True
                reply = await self.call("commit", {"token": token, "text": text})
                expected = self.args["node"]["text"] + text
                if (
                    reply.get("status") == "verified"
                    and reply.get("after_hash") == hashlib.sha256(expected.encode()).hexdigest()
                    and reply.get("utf16_length") == len(expected.encode("utf-16-le")) // 2
                ):
                    return ActionResult(
                        Outcome.OK,
                        "Unicode editor append verified",
                        Effect.APPLIED,
                        verification="unicode_text_appended",
                    )
                if reply.get("status") == "denied":
                    return ActionResult(
                        Outcome.DENIED, "Editor changed or user intervened", error_code="android_unicode_editor_changed"
                    )
                raise ValueError("Unicode append outcome is unverified")
            raise ValueError("Unknown Unicode operation")
        except Exception:  # noqa: BLE001 -- never expose pairing keys, editor text or native exception messages
            return (
                ActionResult(
                    Outcome.UNKNOWN,
                    "Unicode/IME outcome requires owner review",
                    Effect.UNKNOWN,
                    error_code="android_unicode_unknown",
                )
                if attempted
                else ActionResult(
                    Outcome.DENIED, "Unicode helper or editor is unavailable", error_code="android_unicode_unavailable"
                )
            )
