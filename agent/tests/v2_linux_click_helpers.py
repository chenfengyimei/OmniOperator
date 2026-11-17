"""Accurate component hit and physical-pixel fixture composed with real native pointer proof/readers."""

import uuid

from v2_linux_button_packet_helpers import ButtonPeer
from v2_linux_control_helpers import EDIT, Model
from v2_linux_desktop_helpers import APP, WINDOW
from v2_linux_key_helpers import Bundle, ProfileBus, profile_factory

from omni.adapters.platform.linux_atspi import COMPONENT, AtspiDesktop
from omni.adapters.platform.linux_semantic import Semantics
from omni.adapters.platform.linux_session import SessionInspector
from omni.domain.desktop import VERSION
from omni.domain.foreground import POLICY


def model():
    value = Model()
    value.nodes[WINDOW]["flags"][0] |= 1 << 1
    value.nodes[WINDOW]["bounds"] = [0, 0, 640, 480]
    value.nodes[EDIT]["bounds"] = [5, 7, 11, 11]
    value.nodes[EDIT]["flags"][1] |= 1 << (41 - 32)
    value.pointer_mutations = []
    value.click_state_bit = 4
    value.point_fault = None
    value.point_hook = None
    return value


class ClickBus(ProfileBus):
    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        model = self.factory.model
        if destination == APP and interface == COMPONENT:
            if model.point_hook is not None:
                await model.point_hook(path, member, body)
            fault = model.point_fault
            if member == "GetExtents":
                assert signature == "u" and body in ([0], [1])
                x, y, w, h = model.nodes[path]["bounds"]
                if body == [0]:
                    x, y = x + 100, y + 200
                if fault == "scale" and path == WINDOW:
                    w //= 2
                if fault == "offset" and path == EDIT and body == [1]:
                    x += 1
                return [[x, y, w, h]]
            if member == "Contains":
                assert signature == "iiu"
                return [fault != "contains"]
            if member == "GetAccessibleAtPoint":
                assert signature == "iiu"
                selected = path if fault == "self" else "/button" if fault == "overlay" else EDIT
                return [[":1.999" if fault == "bus" else APP, selected]]
        return await super().call(destination, path, interface, member, signature, body, returns=returns)


def click_factory(value):
    selected = profile_factory(value)
    selected.buses["a11y"] = ClickBus(selected, "a11y")
    return selected


class ClickPeer(ButtonPeer):
    def send(self, data):
        if not self.stream and len(data) >= 5 and data[4] == 6:
            callback, self.after_move = self.after_move, None
            try:
                result = super().send(data)
                self.switch = False
                return result
            finally:
                self.after_move = callback
        return super().send(data)


class ClickBundle(Bundle):
    def __init__(self, value, *, selected_factory=None):
        super().__init__(value, selected_factory=selected_factory or click_factory(value))
        self.input = ClickPeer()
        self.input.pointer_mode = True
        self.input.after_move = self.apply

    def apply(self):
        self.model.pointer_mutations.append("click")
        if self.effect == "normal":
            self.model.nodes[EDIT]["flags"][0] ^= 1 << self.model.click_state_bit
        elif self.effect == "foreign_value":
            self.model.nodes[EDIT]["text"] = "unrelated change"


async def command(value):
    selected = click_factory(value)
    desktop = AtspiDesktop(SessionInspector(selected.ops, selected))
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
            "kind": "click",
            "text": "",
            "node": proof,
            "policy": POLICY,
            "takeover": True,
            **{name: str(uuid.uuid4()) for name in ("grant_id", "run_id", "operation_id")},
        },
    }
