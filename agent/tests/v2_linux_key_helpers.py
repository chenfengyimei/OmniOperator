"""AT-SPI model plus independent XKB/XRes/XTEST binary peers, with no native sockets or input."""

import uuid

from test_v2_linux_key_switch import SwitchPeer
from test_v2_linux_window_binding import NativePeer
from v2_linux_control_helpers import EDIT, ControlBus, Model, factory
from v2_linux_desktop_helpers import APP, SESSION, WINDOW
from v2_x11_helpers import WINDOW as XWINDOW
from v2_x11_helpers import OwnedConnection

from omni.adapters.platform.linux_atspi import COMPONENT, AtspiDesktop
from omni.adapters.platform.linux_bus import Process, RemoteError
from omni.adapters.platform.linux_input import InputWire, X11Input
from omni.adapters.platform.linux_keyboard import SYMBOLS
from omni.adapters.platform.linux_semantic import Semantics
from omni.adapters.platform.linux_session import ACCESSIBLE, SessionInspector
from omni.adapters.platform.linux_window_binding import WindowBinding
from omni.adapters.platform.linux_window_leases import LeaseWire
from omni.adapters.platform.linux_x11 import X11Desktop
from omni.domain.desktop import VERSION
from omni.domain.foreground import POLICY


def model():
    value = Model()
    value.typing = True
    value.nodes[WINDOW]["flags"][0] |= 1 << 1
    value.nodes[EDIT]["flags"][0] |= (1 << 11) | (1 << 12)
    value.key_mutations = []
    return value


class ProfileBus(ControlBus):
    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        if interface == COMPONENT and member == "GetExtents" and body == [0]:
            raise RemoteError("fixture_screen_geometry_not_implemented")
        if (destination, path, interface, member) == (APP, WINDOW, ACCESSIBLE, "GetAttributes"):
            self.check()
            return [dict(self.factory.attributes)]
        return await super().call(destination, path, interface, member, signature, body, returns=returns)


def profile_factory(value):
    f = factory(value)
    f.attributes = {"toolkit": "Avalonia", "avalonia-window-handle": str(XWINDOW)}
    f.ops.processes[30] = Process(30, 0, 130.0, "/usr/lib/xorg/Xorg")
    f.sessions[30] = SESSION
    f.buses["a11y"] = ProfileBus(f, "a11y")
    return f


async def command(value, key="Left"):
    f = profile_factory(value)
    desktop = AtspiDesktop(SessionInspector(f.ops, f))
    await desktop.setup()
    try:
        target = await desktop.target((APP, WINDOW))
        proof = (await Semantics(desktop).resolve(target, [0]))[-1]
    finally:
        assert await desktop.close()
    return {
        "version": VERSION,
        "request_id": str(uuid.uuid4()),
        "operation": "foreground",
        "limit": 1,
        "target": target,
        "action": {
            "kind": "press_key",
            "text": key,
            "node": proof,
            "policy": POLICY,
            "takeover": True,
            **{name: str(uuid.uuid4()) for name in ("grant_id", "run_id", "operation_id")},
        },
    }


class Bundle:
    def __init__(self, value, key="Left", *, selected_factory=None):
        self.model, self.key = value, key
        self.factory = selected_factory or profile_factory(value)
        self.input, self.native = SwitchPeer(), NativePeer()
        selected = next(code for code, row in self.input.rows.items() if row[0] == SYMBOLS[key])
        self.input.rows[40], self.input.rows[selected] = self.input.rows[selected], self.input.rows[40]
        self.connections, self.monitors, self.bindings = [], [], []
        self.input.after_copy = self.apply
        self.effect = "normal"

    def apply(self):
        value, key = self.model, self.key
        value.key_mutations.append(key)
        before = value.nodes[EDIT]["text"]
        caret = len(before) if value.caret is None else value.caret
        if self.effect != "normal":
            if self.effect == "foreign_value":
                value.nodes[EDIT]["text"] = "unrelated result"
            return
        # Independently encode fixture application behavior, not the expected
        # result function under test. No native keyboard or platform API.
        if key == "Left":
            value.caret = max(0, caret - 1)
        elif key == "Right":
            value.caret = min(len(before), caret + 1)
        elif key == "Home":
            value.caret = before.rfind("\n", 0, caret) + 1
        elif key == "End":
            end = before.find("\n", caret)
            value.caret = len(before) if end < 0 else end
        elif key == "Backspace" and caret:
            value.nodes[EDIT]["text"] = before[: caret - 1] + before[caret:]
            value.caret = caret - 1
        elif key == "Delete" and caret < len(before):
            value.nodes[EDIT]["text"] = before[:caret] + before[caret + 1 :]
        elif key == "Enter":
            value.nodes[EDIT]["text"] = before[:caret] + "\n" + before[caret:]
            value.caret = caret + 1

    def monitor(self, inspector):
        async def connect(selected):
            connection = OwnedConnection(selected)
            connection.send = self.input.send
            self.input.owner = connection
            self.monitors.append(connection)
            return connection

        def wire(connection, check):
            value = InputWire(connection, check)
            value.loop = self.input
            return value

        return X11Input(inspector, connection_factory=connect, wire_factory=wire)

    def binding(self, desktop, monitor):
        async def connect(selected):
            connection = OwnedConnection(selected)
            self.connections.append(connection)
            return connection

        def wire(connection, check):
            value = LeaseWire(connection, check)
            value.loop = self.native
            return value

        def native(inspector):
            return X11Desktop(inspector, connection_factory=connect, wire_factory=wire)

        result = WindowBinding(desktop, monitor, native_factory=native)
        self.bindings.append(result)
        return result

    @property
    def arguments(self):
        return {
            "ops": self.factory.ops,
            "factory": self.factory,
            "monitor_factory": self.monitor,
            "binding_factory": self.binding,
        }
