"""Reusable remote_desktop fixtures, extracted without collecting test cases."""

import asyncio
import uuid

import pytest

from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_portal import PATH, PORTAL, REQUEST, SESSION, PortalSession
from omni.adapters.platform.desktop.linux.linux_remote_desktop import REMOTE_DESKTOP, ControlSession
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.desktop_control import run as worker
from omni.domain.contracts import BusinessError
from omni.domain.desktop_control import PURPOSE, VERSION, command
from omni.domain.desktop_control import event as control_event
from omni.domain.desktop_portal import decode, encode
from omni_test_support.v2.v2_linux_desktop_helpers import variant
from omni_test_support.v2.v2_portal_helpers import PortalBus, portal_factory


class ControlBus(PortalBus):
    control_version = 2
    available_devices = 3

    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        if destination != self.names[PORTAL] or interface != REMOTE_DESKTOP and member != "GetAll" or member == "Close":
            return await super().call(destination, path, interface, member, signature, body, returns=returns)
        if member == "GetAll" and body != [REMOTE_DESKTOP]:
            return await super().call(destination, path, interface, member, signature, body, returns=returns)
        self.check()
        self.requests.append((path, interface, member, body))
        if member == "GetAll":
            return [
                {
                    "version": variant("u", self.control_version),
                    "AvailableDeviceTypes": variant("u", self.available_devices),
                }
            ]
        options = body[-1]
        handle = f"{PATH}/request/1_99/{options['handle_token'].value}"
        result, code = {}, 0
        if member == "CreateSession":
            assert signature == "a{sv}"
            self.session = f"{PATH}/session/1_99/{options['session_handle_token'].value}"
            result = {"session_handle": variant("s", self.session)}
        elif member == "SelectDevices":
            assert signature == "oa{sv}" and body[0] == self.session
            self.selected_devices = options["types"].value
            assert set(options) == {"handle_token", "types", "persist_mode"} and options["persist_mode"].value == 0
        elif member == "Start":
            assert signature == "osa{sv}" and body[:2] == [self.session, ""]
            self.starting.set()
            if self.hold_start:
                return [handle]
            result = {
                "devices": variant(
                    "u", 1 if self.fault == "partial" else 7 if self.fault == "excess" else self.selected_devices
                ),
                "clipboard_enabled": variant("b", self.fault == "clipboard"),
            }
            if self.fault == "restore":
                result["restore_token"] = variant("s", "private-token-never-export")
            if self.fault == "streams":
                result["streams"] = variant("a(ua{sv})", [[1, {}]])
            code = 1 if self.fault == "denied" else 0
        else:
            pytest.fail("Control consent may not dispatch input, clipboard or ScreenCast operations")
        self.emit(handle, REQUEST, "Response", "ua{sv}" if self.fault != "signature" else "ss", [code, result])
        return [handle if self.fault != "handle" else "/wrong/request"]


def factory():
    value = portal_factory()
    value.buses["user"] = ControlBus(value)
    return value


def opening(mask=3):
    return {
        "version": VERSION,
        "binding": {key: str(uuid.uuid4()) for key in ("agent_id", "view_id", "boot_id", "grant_id")},
        "command": "open",
        "purpose": PURPOSE,
        "devices": mask,
        "consent": True,
    }


__all__ = [
    "BusinessError",
    "ControlBus",
    "ControlSession",
    "PATH",
    "PORTAL",
    "PURPOSE",
    "PortalBus",
    "PortalSession",
    "Process",
    "REMOTE_DESKTOP",
    "REQUEST",
    "SESSION",
    "SessionInspector",
    "VERSION",
    "asyncio",
    "command",
    "control_event",
    "decode",
    "encode",
    "factory",
    "opening",
    "portal_factory",
    "pytest",
    "uuid",
    "variant",
    "worker",
]
