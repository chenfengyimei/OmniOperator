"""Exercise real GstConsumer against an injected GI surface; native GStreamer/desktop remains untested."""

import threading
from types import SimpleNamespace as NS

import pytest

from omni.adapters.platform import linux_pipewire as p
from omni.domain.contracts import BusinessError


def native(*, fault=None):
    state = {"properties": {}, "mapped": False, "closed": False, "written": None}
    info = NS(width=2, height=2, stride=[8], offset=[0], finfo=NS(format=1, n_planes=1))
    if fault == "format":
        info.finfo.format = 2
    if fault == "extent":
        info.width = 8192
    raw = b"\xff\0\0\0\xff\0\0\0" + b"\0\0\xff\xff\xff\xff\0\0"
    mapping = NS(data=raw)

    def mapped(_flags):
        state["mapped"] = True
        return True, mapping

    def unmapped(value):
        assert value is mapping
        state["mapped"] = False

    buffer = NS(get_size=lambda: len(raw), map=mapped, unmap=unmapped)
    sample = NS(get_caps=lambda: "caps", get_buffer=lambda: buffer)

    class Element:
        def __init__(self, name):
            self.name = name
            self.values = {}
            state["properties"].setdefault(name, []).append(self.values)

        def set_property(self, key, value):
            self.values[key] = value

        def link(self, right):
            return fault != "link"

        def try_pull_sample(self, timeout):
            assert timeout == 100_000_000
            return None if fault == "timeout" else sample

    class Pipeline:
        def add(self, part):
            return None

        def set_state(self, value):
            if value == 0:
                assert not state["mapped"]
                state["closed"] = True
            return 1

        def get_state(self, timeout):
            assert timeout == 1_000_000_000
            return (2 if fault == "cleanup" else 1), 0, None

        def get_bus(self):
            return NS(pop_filtered=lambda flags: object() if fault == "stream" else None)

    Gst = NS(
        Pipeline=NS(new=lambda name: Pipeline()),
        ElementFactory=NS(make=lambda name, _id: Element(name)),
        Caps=NS(from_string=lambda value: value),
        State=NS(PLAYING=4, NULL=0),
        StateChangeReturn=NS(FAILURE=0, SUCCESS=1),
        MessageType=NS(ERROR=1, EOS=2),
        MSECOND=1_000_000,
        SECOND=1_000_000_000,
        MapFlags=NS(READ=1),
    )
    Video = NS(
        VideoInfo=NS(new_from_caps=lambda caps: info), VideoFormat=NS(RGB=1), buffer_get_video_meta=lambda buffer: None
    )
    consumer = p.GstConsumer()
    consumer.Gst, consumer.Video = Gst, Video
    return consumer, state


@pytest.mark.parametrize("fault", [None, "format", "extent", "link", "timeout", "stream", "cleanup", "revoked"])
def test_serial_target_fd_bounded_buffers_and_teardown_without_default_remote(tmp_path, monkeypatch, fault):
    consumer, state = native(fault=fault)
    stopped = threading.Event()
    if fault == "revoked":
        stopped.set()

    def publish(path, data):
        assert not state["mapped"]
        state["written"] = data

    monkeypatch.setattr(p, "publish", publish)
    remote = NS(fileno=lambda: 64)
    if fault is None:
        receipt = consumer.read(remote, 2**54 + 55, tmp_path / "frame.png", stopped)
        assert receipt["method"] == "pipewire_window" and receipt["width"] == 2
        props = state["properties"]["pipewiresrc"][0]
        assert props["target-object"] == str(2**54 + 55) and props["fd"] == 64
        assert props["dont-reconnect"] is True and props["min-buffers"] == props["max-buffers"] == 2
        assert "path" not in props and "node-id" not in props
        assert state["properties"]["appsink"][0]["max-buffers"] == 1
    else:
        with pytest.raises(BusinessError):
            consumer.read(remote, 123456, tmp_path / "frame.png", stopped)
        if fault != "cleanup":
            assert state["written"] is None
    assert state["closed"] and not state["mapped"]
