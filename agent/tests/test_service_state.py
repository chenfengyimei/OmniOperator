"""服务运行态快照（data/run/<name>.state.json）读写协议。

重点是"读端永不抛异常"和"新鲜度判定"：工作台每 5 秒读一次，
读到坏文件、旧实例的快照或过期数字时，必须降级成可见的"不新鲜"，
而不是让接口 500 或者把陈旧数字当真。
"""

import json
import time

from omni.system.process_identity import ProcessIdentity
from omni.system.service_state import (
    StateWriter,
    read_state,
    state_path,
)
from omni.system.services import ServiceManager


def _writer(tmp_path, *, service="omni-heartbeat", launch_id="a" * 32, clock=time.time):
    return StateWriter(state_path(tmp_path, service), service=service, launch_id=launch_id, pid=4242, clock=clock)


def test_state_path_uses_run_dir(tmp_path):
    assert state_path(tmp_path, "omni-qq").name == "omni-qq.state.json"


def test_roundtrip_is_fresh(tmp_path):
    writer = _writer(tmp_path)
    writer.write(stats={"ticks": 3, "last_tick_at": "2026-11-01T10:00:00+08:00"})
    snap = read_state(state_path(tmp_path, "omni-heartbeat"), launch_id="a" * 32)
    assert snap["fresh"] is True and snap["reason"] == "ok"
    assert snap["stats"]["ticks"] == 3
    assert snap["seq"] == 1 and snap["pid"] == 4242


def test_seq_increases_per_write(tmp_path):
    writer = _writer(tmp_path)
    writer.write()
    writer.write(stats={"ticks": 2})
    assert read_state(state_path(tmp_path, "omni-heartbeat"))["seq"] == 2


def test_missing_file_is_not_an_error(tmp_path):
    snap = read_state(state_path(tmp_path, "omni-qq"))
    assert snap["present"] is False and snap["fresh"] is False
    assert snap["reason"] == "missing" and snap["stats"] == {}


def test_stale_after_ttl(tmp_path):
    now = 1_000.0
    writer = _writer(tmp_path, clock=lambda: now)
    writer.write(stats={"ticks": 1})
    path = state_path(tmp_path, "omni-heartbeat")
    assert read_state(path, now=now + 10, ttl_s=45)["fresh"] is True
    stale = read_state(path, now=now + 60, ttl_s=45)
    assert stale["fresh"] is False and stale["reason"] == "stale"
    # 过期不等于数据不存在：仍要把最后一次自报的数字给出来
    assert stale["stats"]["ticks"] == 1 and stale["age_s"] == 60


def test_launch_id_mismatch_is_not_fresh(tmp_path):
    """重启后换了实例，旧快照描述的不是现在这个进程。"""
    writer = _writer(tmp_path, launch_id="a" * 32)
    writer.write(stats={"ticks": 9})
    snap = read_state(state_path(tmp_path, "omni-heartbeat"), launch_id="b" * 32)
    assert snap["fresh"] is False and snap["reason"] == "launch_mismatch"


def test_corrupt_file_never_raises(tmp_path):
    path = state_path(tmp_path, "omni-qq")
    path.parent.mkdir(parents=True, exist_ok=True)
    for broken in ("{半截", "[]", '{"version": 99}', '{"version": 1, "updated": "x"}'):
        path.write_text(broken, encoding="utf-8")
        snap = read_state(path)
        assert snap["fresh"] is False and snap["reason"] == "corrupt"


def test_oversized_file_is_treated_as_corrupt(tmp_path):
    path = state_path(tmp_path, "omni-qq")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "updated": 1.0, "pad": "x" * 70000}), encoding="utf-8")
    assert read_state(path)["reason"] == "corrupt"


def _manager(tmp_path, identity):
    return ServiceManager(
        napcat_dir=None,
        napcat_ws_port=1,
        napcat_webui_port=2,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        port_prober=lambda _port, _state: [],
        tcp_probe=lambda _port: False,
        inspector=lambda pid: identity if pid == identity.pid else None,
    )


def test_status_merges_service_state(tmp_path):
    """status() 是前端唯一的数据源：运行态要跟着一起出来。"""
    executable = str(tmp_path / "python.exe")
    identity = ProcessIdentity(4242, "100", executable)
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    (tmp_path / "run" / "omni-heartbeat.pid").write_text(
        json.dumps(
            {
                "version": 1,
                "service": "omni-heartbeat",
                "launch_id": "c" * 32,
                "pid": 4242,
                "created": "100",
                "executable": executable,
            }
        ),
        encoding="utf-8",
    )
    manager = _manager(tmp_path, identity)
    StateWriter(
        state_path(tmp_path / "run", "omni-heartbeat"), service="omni-heartbeat", launch_id="c" * 32, pid=4242
    ).write(stats={"ticks": 7, "fired_total": 2})
    status = manager.status("omni-heartbeat")
    assert status["running"] is True and status["fresh"] is True
    assert status["stats"]["ticks"] == 7
    assert status["health_reason"] == "ok"


def test_status_flags_stale_service_state(tmp_path):
    executable = str(tmp_path / "python.exe")
    identity = ProcessIdentity(4242, "100", executable)
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    (tmp_path / "run" / "omni-qq.pid").write_text(
        json.dumps(
            {
                "version": 1,
                "service": "omni-qq",
                "launch_id": "d" * 32,
                "pid": 4242,
                "created": "100",
                "executable": executable,
            }
        ),
        encoding="utf-8",
    )
    manager = ServiceManager(
        napcat_dir=None,
        napcat_ws_port=1,
        napcat_webui_port=2,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        port_prober=lambda _port, _state: [],
        tcp_probe=lambda _port: False,
        inspector=lambda pid: identity if pid == 4242 else None,
        health_timeout_s=15,
    )
    (tmp_path / "run" / "omni-qq.state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "service": "omni-qq",
                "launch_id": "d" * 32,
                "pid": 4242,
                "updated": 1.0,
                "seq": 1,
                "health": "ok",
                "stats": {"received": 3},
            }
        ),
        encoding="utf-8",
    )
    status = manager.status("omni-qq")
    assert status["fresh"] is False and status["health_reason"] == "stale"
    # 数字仍然给出来，界面负责标黄，别静默丢掉最后一次已知值
    assert status["stats"]["received"] == 3


def test_napcat_has_no_state_file(tmp_path):
    manager = ServiceManager(
        napcat_dir=None,
        napcat_ws_port=1,
        napcat_webui_port=2,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        port_prober=lambda _port, _state: [],
        tcp_probe=lambda _port: False,
    )
    status = manager.status("napcat")
    assert status["fresh"] is False and status["health_reason"] == "not_applicable"
