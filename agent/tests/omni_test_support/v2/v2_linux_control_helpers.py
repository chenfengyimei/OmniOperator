"""AT-SPI application, input guard and owned worker pipes; all peers are injected, never native devices."""

import asyncio
import copy
import json

from omni.adapters.platform.desktop.linux.linux_atspi import COMPONENT, TEXT
from omni.adapters.platform.desktop.linux.linux_bus import RemoteError, failure
from omni.adapters.platform.desktop.linux.linux_semantic import ACTION, EDITABLE, SELECTION
from omni.adapters.platform.desktop.linux.linux_session import ACCESSIBLE, PROPERTIES
from omni.adapters.workers.desktop import decode, encode
from omni.adapters.workers.linux_action import run
from omni.adapters.workers.linux_desktop import perform
from omni_test_support.v2.v2_linux_desktop_helpers import APP, ROOT, WINDOW, Bus, Factory, node, variant

EDIT, BUTTON, LIST, ITEM = "/edit", "/button", "/list", "/item"


class Model:
    def __init__(self):
        self.nodes = {
            WINDOW: node(23, name="fixture-window", children=[[APP, EDIT], [APP, BUTTON], [APP, LIST]]),
            EDIT: node(61, name="fixture-edit", content="before"),
            BUTTON: node(43, name="fixture-button"),
            LIST: node(31, name="fixture-list", children=[[APP, ITEM]]),
            ITEM: node(32, name="fixture-item"),
        }
        self.nodes[EDIT]["interfaces"].append(EDITABLE)
        self.nodes[EDIT]["flags"][0] |= 1 << 7
        self.nodes[BUTTON]["interfaces"].append(ACTION)
        self.nodes[LIST]["interfaces"] = [COMPONENT, SELECTION]
        self.nodes[ITEM]["flags"][0] |= 1 << 22
        self.parents = {WINDOW: ROOT, EDIT: WINDOW, BUTTON: WINDOW, LIST: WINDOW, ITEM: LIST}
        self.mutations = []
        self.actions = ["click"]
        self.selected = False
        self.reply = "normal"
        self.hook = None
        self.monitors = []
        self.typing = False
        self.caret = None
        self.selections = 0
        self.advance_caret = True


class ControlBus(Bus):
    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        f = self.factory
        model = f.model
        if self.kind == "a11y" and destination == APP:
            if interface == ACCESSIBLE and member == "GetAttributes":
                raise RemoteError("fixture_native_window_attributes_not_implemented")
            if interface == PROPERTIES and body == [TEXT, "CaretOffset"]:
                if not model.typing:
                    raise RemoteError("fixture_optional_text_not_supported")
                return [variant("i", len(model.nodes[path]["text"]) if model.caret is None else model.caret)]
            if interface == TEXT and member == "GetNSelections":
                if not model.typing:
                    raise RemoteError("fixture_optional_text_not_supported")
                return [model.selections]
            if interface == PROPERTIES and member == "Get" and body == [ACCESSIBLE, "Parent"]:
                return [variant("(so)", [APP, model.parents[path]])]
            if interface == PROPERTIES and member == "Get" and body == [ACTION, "NActions"]:
                return [variant("i", len(model.actions))]
            if interface == ACTION and member == "GetName":
                return [model.actions[body[0]]]
            if member in {"SetTextContents", "SelectChild", "DoAction", "InsertText"}:
                self.check()
                model.mutations.append((member, copy.deepcopy(body)))
                if model.hook:
                    model.hook(member)
                if member == "SetTextContents" and model.reply != "unchanged":
                    model.nodes[path]["text"] = body[0]
                if member == "InsertText":
                    position, text, length = body
                    assert model.typing and signature == "isi" and length == len(text.encode("utf-8"))
                    if model.reply != "unchanged":
                        original = model.nodes[path]["text"]
                        model.nodes[path]["text"] = original[:position] + text + original[position:]
                    model.caret = position + len(text) if model.advance_caret else position
                if member == "SelectChild":
                    assert path == LIST and body == [0]
                    model.selected = True
                if model.reply == "lost":
                    raise RemoteError("private-native-canary")
                return [False if model.reply == "false" else True]
            if member == "IsChildSelected":
                return [model.selected]
        return await super().call(destination, path, interface, member, signature, body, returns=returns)


def factory(model=None, *, wayland=False):
    f = Factory()
    f.model = model or Model()
    f.nodes = f.model.nodes
    f.buses["a11y"] = ControlBus(f, "a11y")
    if not wayland:
        f.session.update(Type=variant("s", "x11"), Display=variant("s", ":0"))
    return f


class Guard:
    def __init__(self, inspector, *, model, fault=None):
        self.inspector, self.model, self.fault = inspector, model, fault
        self.checked = 0
        self.opened = self.armed = self.closed = False
        model.monitors.append(self)

    async def open(self):
        self.opened = True
        if self.fault == "missing":
            raise failure("desktop_input_unverified")

    async def arm(self):
        self.armed = True
        if self.fault == "held":
            raise failure("desktop_input_held")

    async def check(self):
        self.checked += 1
        if self.fault == "changed":
            raise failure("desktop_user_intervened")

    async def close(self):
        self.closed = True
        return self.fault != "cleanup"


class Process:
    def __init__(self, model, *, guard_fault=None):
        self.input = asyncio.StreamReader(limit=65536)
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdin = Input(self)
        self.returncode = None

        async def send(frame):
            self.stdout.feed_data(frame)

        async def worker():
            f = factory(model)
            injected = {
                "ops": f.ops,
                "factory": f,
                "monitor_factory": lambda inspector: Guard(inspector, model=model, fault=guard_fault),
            }
            try:
                initial = await self.input.readline()
                request = decode(initial)
                if request["operation"] in {"act", "foreground"}:
                    replay = asyncio.StreamReader(limit=65536)
                    replay.feed_data(initial)

                    # Forward the actual parent permit, not an automatically fabricated one.
                    async def forward():
                        while frame := await self.input.readline():
                            replay.feed_data(frame)
                        replay.feed_eof()

                    forwarder = asyncio.create_task(forward())
                    try:
                        await run(replay, send, **injected)
                    finally:
                        forwarder.cancel()
                        await asyncio.gather(forwarder, return_exceptions=True)
                else:
                    await send(encode(await perform(request, **injected), request))
                self.returncode = 0
            finally:
                self.stdout.feed_eof()
                self.stderr.feed_eof()

        self.worker = asyncio.create_task(worker())

    async def wait(self):
        await self.worker
        return self.returncode


class Input:
    def __init__(self, process):
        self.process, self.frames, self.closed = process, [], False

    def write(self, frame):
        self.frames.append(json.loads(frame))
        self.process.input.feed_data(frame)

    async def drain(self):
        return None

    def close(self):
        if not self.closed:
            self.closed = True
            self.process.input.feed_eof()


class Owned:
    def __init__(self, model, *, fault=None):
        self.process = Process(model, guard_fault=fault)
        self.reclaimed = False

    async def terminate(self, _timeout):
        self.process.stdin.close()
        await self.process.worker
        self.reclaimed = True
        return True


class Spawn:
    def __init__(self, model=None, *, fault=None):
        self.model, self.fault = model or Model(), fault
        self.calls, self.owners = [], []

    async def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        owned = Owned(self.model, fault=self.fault)
        self.owners.append(owned)
        return owned
