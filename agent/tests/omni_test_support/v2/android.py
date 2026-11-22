"""Reusable android fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import io
import json
import struct
import uuid
from contextlib import asynccontextmanager
from xml.sax.saxutils import escape

import pytest
from PIL import Image
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.adapters.workers.android import hierarchy
from omni.composition.instance import open_instance
from omni.composition.lifecycle import close_until_quiescent
from omni.domain.android import action_request, connection
from omni.domain.contracts import BusinessError
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.v2_action_review_helpers import review_request

SERIAL = "isolated-device-01"

BOOT = "13a93fb1-829e-4d44-a185-e1a07dc75401"


class FakeAdb:
    def __init__(self):
        self.boot, self.user, self.transport = BOOT, 0, 7
        self.text, self.checked, self.locked = "hello", False, False
        self.package, self.activity = "org.fixture", ".MainActivity"
        self.packages = ["org.fixture", "org.second", "org.private"]
        self.ignore_launch = False
        self.scrolling, self.scroll_offset = False, 0
        self.writes, self.services = [], []
        self.drop_after_input = False
        self.malformed = False
        self.allow_dump = asyncio.Event()
        self.allow_dump.set()
        self.dump_entered = asyncio.Event()
        self.allow_capture = asyncio.Event()
        self.allow_capture.set()
        self.capture_entered = asyncio.Event()
        self.clients = set()
        self.errors = []
        picture = io.BytesIO()
        Image.new("RGB", (32, 24), "green").save(picture, format="PNG")
        self.png = picture.getvalue()

    def xml(self):
        if self.malformed:
            return b"no hierarchy"
        if self.scrolling:
            offset = self.scroll_offset
            return (
                f'<?xml version="1.0"?><hierarchy><node package="{self.package}" class="android.widget.ScrollView" resource-id="org.fixture:id/scroll" bounds="[0,0][200,400]" enabled="true" scrollable="true" password="false">'
                f'<node package="{self.package}" class="android.widget.TextView" resource-id="org.fixture:id/first" text="first" bounds="[10,{60 + offset}][100,{90 + offset}]" password="false" enabled="true"/>'
                f'<node package="{self.package}" class="android.widget.TextView" resource-id="org.fixture:id/second" text="second" bounds="[10,{100 + offset}][100,{130 + offset}]" password="false" enabled="true"/>'
                "</node></hierarchy>"
            ).encode()
        return (
            (
                f'<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">'
                f'<node package="org.fixture" class="android.widget.EditText" resource-id="org.fixture:id/input" '
                f'text="{escape(self.text, {chr(34): "&quot;"})}" content-desc="editor" bounds="[0,0][100,40]" '
                f'password="false" enabled="true" focused="true" clickable="true" long-clickable="true" scrollable="true" checkable="false" checked="false" />'
                f'<node package="org.fixture" class="android.widget.CheckBox" resource-id="org.fixture:id/check" '
                f'text="check" content-desc="" bounds="[0,50][100,100]" password="false" enabled="true" '
                f'focused="false" clickable="true" checkable="true" checked="{str(self.checked).lower()}" />'
                '<node package="org.fixture" class="android.widget.EditText" resource-id="password" '
                'text="private-password-value" content-desc="private-password-description" bounds="[0,110][100,150]" '
                'password="true" enabled="true" focused="false" clickable="true" /></hierarchy>'
            )
            .replace('package="org.fixture"', f'package="{self.package}"')
            .encode()
        )

    async def service(self, reader):
        size = int(await reader.readexactly(4), 16)
        if size > 4096:
            raise ValueError("Oversized fake service")
        value = (await reader.readexactly(size)).decode()
        self.services.append(value)
        return value

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.clients.add(task)
        try:
            service = await self.service(reader)
            if service == "host:devices-l":
                payload = f"{SERIAL}\tdevice product:fixture transport_id:{self.transport}\n".encode()
                writer.write(b"OKAY" + f"{len(payload):04x}".encode() + payload)
                await writer.drain()
                return
            if service != f"host:transport-id:{self.transport}":
                writer.write(b"FAIL0000")
                await writer.drain()
                return
            writer.write(b"OKAY")
            await writer.drain()
            command = await self.service(reader)
            if command.startswith("localabstract:"):
                writer.write(b"OKAY")
                await writer.drain()
                await self.helper(reader, writer, command)
                return
            assert command.startswith("shell,v2,raw:"), command
            command = command.removeprefix("shell,v2,raw:")
            writer.write(b"OKAY")
            await writer.drain()
            payload = b""
            if command == "cat /proc/sys/kernel/random/boot_id":
                payload = (self.boot + "\n").encode()
            elif command == "am get-current-user":
                payload = str(self.user).encode()
            elif command == "getprop ro.build.fingerprint":
                payload = b"fixture/product/device:14/fixture/release"
            elif command == "dumpsys window windows":
                payload = f"mCurrentFocus=Window{{abcd u{self.user} {self.package}/{self.activity}}}\n".encode()
            elif command == "dumpsys window policy":
                flag = str(self.locked).lower()
                payload = f"mAwake=true\nKeyguardServiceDelegate\n  showing={flag}\n  inputRestricted={flag}\n".encode()
            elif command == "uiautomator dump /proc/self/fd/1":
                self.dump_entered.set()
                await self.allow_dump.wait()
                payload = self.xml()
            elif command == "screencap -p":
                self.capture_entered.set()
                await self.allow_capture.wait()
                payload = self.png
            elif command == f"pm list packages --user {self.user}":
                payload = ("\n".join("package:" + p for p in self.packages) + "\n").encode()
            elif command.startswith(f"cmd package resolve-activity --brief --user {self.user} "):
                destination = command.rsplit(" -p ", 1)[1]
                payload = (
                    (destination + "/.MainActivity\n").encode()
                    if destination in self.packages
                    else b"No activity found\n"
                )
            elif command.startswith(f"am start -W --user {self.user} -n '"):
                component = command.split("'", 2)[1]
                self.writes.append(command)
                if not self.ignore_launch:
                    self.package, self.activity = component.split("/", 1)
                payload = b"Status: ok\n"
            elif command.startswith("input swipe ") or command.startswith("input keyevent KEYCODE_"):
                self.writes.append(command)
                if self.scrolling and command.startswith("input swipe "):
                    self.scroll_offset -= 10
            elif command.startswith("input text '") and command.endswith("'"):
                self.writes.append(command)
                self.text += command[len("input text '") : -1].replace("%s", " ")
            elif command == "input tap 50 75":
                self.writes.append(command)
                self.checked = not self.checked
            elif command == "input tap 50 20":
                self.writes.append(command)
            else:
                payload = await self.extra_shell(command)
            if command.startswith("input ") and self.drop_after_input:
                return
            if payload:
                writer.write(struct.pack("<BI", 1, len(payload)) + payload)
            writer.write(struct.pack("<BI", 3, 1) + b"\x00")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception as error:  # noqa: BLE001 -- retain all fake-server failures for the owning test assertion
            self.errors.append(error)
        finally:
            self.clients.discard(task)
            writer.close()
            await writer.wait_closed()

    async def extra_shell(self, command):
        raise AssertionError("Unexpected fake device command: " + command)

    async def helper(self, reader, writer, service):
        raise AssertionError("Unexpected fake native service: " + service)


@asynccontextmanager
async def fake_server():
    fake = FakeAdb()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    try:
        yield fake, server.sockets[0].getsockname()[1]
    finally:
        fake.allow_dump.set()
        fake.allow_capture.set()
        server.close()
        await server.wait_closed()
        if fake.clients:
            await asyncio.wait(fake.clients, timeout=2)
        assert not fake.errors, fake.errors
        assert not fake.clients


@asynccontextmanager
async def prepared(tmp_path):
    async with fake_server() as (fake, port):
        instance = await open_instance(tmp_path / "home", capabilities=())
        agent = (await instance.application.query("agents", ""))[0]["id"]
        try:

            def setup(u):
                config = u.configuration_create(
                    agent,
                    "channel",
                    "Isolated Android",
                    True,
                    {"driver": "android", "endpoint": f"http://127.0.0.1:{port}", "account": SERIAL},
                )
                session = u._record(s.sessions, {"title": "Android fixture", "revision": 1, "next_seq": 0}, agent)
                model = u.configuration_get(agent, u.settings(agent)["value"]["model_profile_id"])
                u.configuration_put(agent, model["id"], model["name"], False, model["value"], model["revision"])
                return config, session["id"]

            config, session = await instance._database.write(setup)
            probe = await instance.android.probe(agent, config["id"], True)
            grant = await instance.android.grant(agent, probe["probe_id"], True)
            yield instance, fake, agent, config, session, grant
        finally:
            fake.allow_dump.set()
            assert (await instance.close())["complete"]


async def request_for(instance, agent, session, grant, kind="append_ascii", text=" world"):
    view = await instance.android.observe(agent, grant["id"])
    node = next(n for n in view["nodes"] if kind in n["actions"])
    return {
        "consent": True,
        "grant_id": grant["id"],
        "observation_id": view["observation_id"],
        "node_id": node["node_id"],
        "session_id": session,
        "request_id": str(uuid.uuid4()),
        "kind": kind,
        "text": text if kind == "append_ascii" else "",
    }, view


async def approve(instance, agent, receipt):
    run = await wait_state(instance.application, agent, receipt["run_id"], {"waiting_approval", "failed"})
    assert run["state"] == "waiting_approval", run
    approval = next(a for a in await instance.application.approvals(agent) if a["run_id"] == receipt["run_id"])
    assert approval["tool"] == "android_input"
    assert approval["arguments"]["target_serial"] == SERIAL
    await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])


__all__ = [
    "BOOT",
    "BusinessError",
    "FakeAdb",
    "Image",
    "SERIAL",
    "action_request",
    "approve",
    "asynccontextmanager",
    "asyncio",
    "close_until_quiescent",
    "connection",
    "escape",
    "fake_server",
    "hierarchy",
    "io",
    "json",
    "open_instance",
    "prepared",
    "pytest",
    "request_for",
    "review_request",
    "s",
    "select",
    "struct",
    "update",
    "uuid",
    "wait_state",
]
