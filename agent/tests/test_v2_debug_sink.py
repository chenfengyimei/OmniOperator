"""Explicit debug capture retains only metadata with finite lifetime, quota and ownership."""

import json
import logging
import os
import threading
import time
import uuid

import pytest

from omni.adapters.diagnostics import JsonLogSink, LoggingSession, Redactor, bind_context
from omni.adapters.temporary_debug import MAX_BYTES, DebugEnvelope, TemporaryDebug
from omni.domain.contracts import BusinessError


def identity():
    return str(uuid.uuid4())


def test_registered_secrets_are_redacted_from_nested_mapping_keys_and_log_records(tmp_path):
    secret = "EXACT-MAPPING-CREDENTIAL-CANARY"
    redactor = Redactor()
    redactor.register(secret)
    result = redactor.value({"nested": [{secret: "value", "access_token": secret}]})
    assert secret not in json.dumps(result)
    assert result["nested"][0]["[redacted]"] == "value"
    sink = JsonLogSink(tmp_path, boot_id=identity(), build_id="a" * 64)
    session = LoggingSession(sink)
    sink.redactor.register(secret)
    try:
        logging.getLogger("omni.v2.test").error("bounded", extra={"error_code": {secret: "value"}})
    finally:
        assert session.close()
    assert secret not in (tmp_path / "runtime.jsonl").read_text()


def test_debug_is_explicit_agent_scoped_metadata_only_and_never_enters_runtime(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id=identity(), build_id="a" * 64)
    session = LoggingSession(sink)
    agent, other, operation = identity(), identity(), identity()
    logger = logging.getLogger("omni.v2.debug_test")

    class PrivateMessage:
        def __str__(self):
            raise AssertionError("Debug must never format a message")

    try:
        with bind_context(agent_id=agent):
            logger.debug(PrivateMessage())
        assert not sink.debug.snapshot()["active"] and not (tmp_path / "debug").exists()
        assert sink.debug.start(agent, operation, 3600)["active"]
        with bind_context(agent_id=other):
            logger.debug("other-agent-canary")
        with bind_context(agent_id=agent, run_id=identity()):
            try:
                raise ValueError("private-exception-canary")
            except ValueError:
                logger.debug(
                    PrivateMessage(),
                    exc_info=True,
                    extra={
                        "event": "span.completed",
                        "duration_ms": 12.0,
                        "outcome": "unknown",
                        "payload": "private-body",
                    },
                )
            logger.info("ordinary entry")
        sink.queue.join()
        status = sink.debug.snapshot()
        assert status["active"] and status["written"] == 1 and status["bytes"] > 0
        debug_content = (tmp_path / "debug" / f"debug-{operation}.jsonl").read_text()
        row = json.loads(debug_content)
        assert row["agent_id"] == agent and row["outcome"] == "unknown" and row["duration_ms"] == 12.0
        assert row["component"] == "omni.v2.debug" and row["event"] == "span.completed"
        assert "timestamp" in row and row["boot_id"] == sink.boot_id
        assert not any(field in row for field in ("message", "exception", "payload"))
        assert "canary" not in debug_content and "private-body" not in debug_content
        runtime = (tmp_path / "runtime.jsonl").read_text()
        assert "ordinary entry" in runtime and '"DEBUG"' not in runtime
    finally:
        assert session.close()


def test_capture_ttl_old_queued_envelopes_and_new_boot_do_not_resume(tmp_path):
    now = [0.0]
    agent, capture = identity(), identity()
    debug = TemporaryDebug(tmp_path / "debug", identity(), lambda: now[0])
    debug.start(agent, capture, 5)
    envelope = debug.admit({"agent_id": agent})
    assert envelope is not None
    now[0] = 5
    assert debug.snapshot()["reason"] == "expired" and not debug.snapshot()["active"]
    assert not debug.write(envelope)
    assert debug.snapshot()["dropped"] == 1 and debug.snapshot()["queued_bytes"] == 0
    debug.start(agent, capture, 5)
    assert not debug.write(envelope)
    current = debug.admit({"agent_id": agent})
    assert current is not None
    changed_boot = DebugEnvelope(current.capture_id, current.agent_id, identity(), current.payload, current.generation)
    assert not debug.write(changed_boot)
    debug.stop()
    restarted = TemporaryDebug(tmp_path / "debug", identity(), lambda: now[0])
    assert not restarted.snapshot()["active"] and restarted.snapshot()["operation_id"] is None
    assert not restarted.write(current)


def test_retained_global_budget_counts_previous_sessions_and_reserved_queue_bytes(tmp_path):
    agent = identity()
    debug = TemporaryDebug(tmp_path / "debug", identity())
    first = identity()
    debug.start(agent, first, 60, byte_limit=1000)
    one = debug.admit({"agent_id": agent})
    assert one and debug.write(one)
    existing = debug.snapshot()["retained_bytes"]
    debug.stop()
    debug.start(agent, identity(), 60, byte_limit=existing + len(one.payload) - 1)
    assert debug.admit({"agent_id": agent}) is None
    status = debug.snapshot()
    assert not status["active"] and status["reason"] == "quota_exhausted"
    assert status["retained_bytes"] == existing
    with pytest.raises(BusinessError) as error:
        debug.start(agent, identity(), 60, byte_limit=existing)
    assert error.value.code == "debug_quota_exhausted"
    debug.start(agent, identity(), 60, byte_limit=existing + len(one.payload) + 5)
    queued = debug.admit({"agent_id": agent})
    assert queued is not None
    assert debug.admit({"agent_id": agent}) is None
    assert debug.snapshot()["queued_bytes"] == len(queued.payload)
    assert not debug.write(queued)
    assert debug.snapshot()["retained_bytes"] <= debug.snapshot()["byte_limit"]
    assert sum(path.stat().st_size for path in debug.directory.iterdir()) == existing


def test_maintenance_only_removes_confirmed_expired_capture_files_and_is_bounded(tmp_path):
    directory = tmp_path / "debug"
    directory.mkdir()
    expired = directory / f"debug-{identity()}.jsonl"
    recent = directory / f"debug-{identity()}.jsonl"
    unrelated = directory / "personal-notes.txt"
    malformed = directory / "debug-not-a-uuid.jsonl"
    for path in (expired, recent, unrelated, malformed):
        path.write_text("preserve unless owned and expired")
    old = time.time() - 86401
    for path in (expired, unrelated, malformed):
        os.utime(path, (old, old))
    debug = TemporaryDebug(directory, identity())
    report = debug.maintenance()
    assert report["removed_files"] == 1 and not expired.exists()
    assert recent.exists() and unrelated.exists() and malformed.exists()
    for index in range(1000):
        (directory / f"unrelated-{index}").touch()
    with pytest.raises(BusinessError) as error:
        debug.maintenance()
    assert error.value.code == "debug_directory_limit"
    assert unrelated.exists() and recent.exists()


def test_capture_rejects_links_invalid_bounds_and_stderr_mode(tmp_path):
    directory = tmp_path / "debug"
    directory.mkdir()
    owned = directory / f"debug-{identity()}.jsonl"
    owned.write_text("owned link target")
    os.link(owned, directory / "other-name")
    debug = TemporaryDebug(directory, identity())
    with pytest.raises(BusinessError) as error:
        debug.maintenance()
    assert error.value.code == "unsafe_debug_path" and owned.exists()
    clean = TemporaryDebug(tmp_path / "clean", identity())
    for duration, limit in ((0, MAX_BYTES), (3601, MAX_BYTES), (True, MAX_BYTES), (10, MAX_BYTES + 1)):
        with pytest.raises(BusinessError) as error:
            clean.start(identity(), identity(), duration, limit)
        assert error.value.code == "invalid_debug_capture"
    sink = JsonLogSink(tmp_path / "stderr", boot_id=identity(), build_id="a" * 64, stderr_only=True)
    try:
        with pytest.raises(BusinessError) as error:
            sink.debug.start(identity(), identity(), 60)
        assert error.value.code == "debug_external_logs" and not (tmp_path / "stderr/debug").exists()
    finally:
        assert sink.shutdown()


def test_debug_queue_overload_stale_records_and_shutdown_are_observable(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id=identity(), build_id="a" * 64, capacity=1)
    session = LoggingSession(sink)
    entered, release = threading.Event(), threading.Event()
    original = sink._write

    def blocked(payload):
        entered.set()
        assert release.wait(10)
        original(payload)

    sink._write = blocked
    agent = identity()
    logger = logging.getLogger("omni.v2.debug_queue")
    try:
        logger.info("block writer")
        assert entered.wait(10)
        sink.debug.start(agent, identity(), 60)
        with bind_context(agent_id=agent):
            logger.debug("queued")
            logger.debug("overloaded")
        assert sink.dropped == 1 and sink.debug.snapshot()["dropped"] == 1
        sink.debug.stop()
        release.set()
        sink.queue.join()
        assert sink.dropped == 2 and sink.debug.snapshot()["dropped"] == 2
        assert sink.debug.snapshot()["queued_bytes"] == 0
        assert not list((tmp_path / "debug").glob("*.jsonl"))
    finally:
        release.set()
        assert session.close()


def test_writer_rechecks_retained_files_and_never_exceeds_global_cap(tmp_path):
    debug = TemporaryDebug(tmp_path / "debug", identity())
    agent = identity()
    debug.start(agent, identity(), 60, byte_limit=1000)
    queued = debug.admit({"agent_id": agent})
    assert queued is not None
    unrelated_capture = debug.directory / f"debug-{identity()}.jsonl"
    unrelated_capture.write_bytes(b"x" * 900)
    assert not debug.write(queued)
    assert debug.snapshot()["reason"] == "quota_exhausted"
    assert sum(path.stat().st_size for path in debug.directory.iterdir()) == 900


def test_writer_permission_failure_is_observable_and_precedes_private_payload(tmp_path, monkeypatch):
    from omni.adapters import temporary_debug

    sink = JsonLogSink(tmp_path, boot_id=identity(), build_id="a" * 64)
    session = LoggingSession(sink)
    agent, capture = identity(), identity()
    try:
        sink.debug.start(agent, capture, 60)

        def denied(*args, **kwargs):
            raise PermissionError("private-permission-canary")

        monkeypatch.setattr(temporary_debug, "private_path", denied)
        with bind_context(agent_id=agent):
            logging.getLogger("omni.v2.debug_permissions").debug("private-payload-canary")
        sink.queue.join()
        assert sink.failures == 1 and sink.dropped == 1
        status = sink.debug.snapshot()
        assert not status["active"] and status["error_code"] == "debug_write_failed"
        assert status["bytes"] == 0 and status["dropped"] == 1
        assert (tmp_path / "debug" / f"debug-{capture}.jsonl").read_bytes() == b""
    finally:
        assert session.close()


def test_slow_disk_does_not_block_admission_snapshot_or_stop(tmp_path, monkeypatch):
    debug = TemporaryDebug(tmp_path / "debug", identity())
    agent = identity()
    debug.start(agent, identity(), 60)
    envelope = debug.admit({"agent_id": agent})
    assert envelope is not None
    entered, release = threading.Event(), threading.Event()
    original = debug._write_file

    def delayed(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(debug, "_write_file", delayed)
    result = []
    writer = threading.Thread(target=lambda: result.append(debug.write(envelope)))
    writer.start()
    try:
        assert entered.wait(10)
        started = time.monotonic()
        assert debug.snapshot()["active"]
        assert debug.admit({"agent_id": agent}) is not None
        assert not debug.stop()["active"]
        assert time.monotonic() - started < 1
    finally:
        release.set()
        writer.join(10)
    assert not writer.is_alive() and result == [True]
    assert debug.snapshot()["written"] == 1


def test_shutdown_during_activation_cannot_enable_a_late_capture(tmp_path, monkeypatch):
    debug = TemporaryDebug(tmp_path / "debug", identity())
    entered, release = threading.Event(), threading.Event()
    original = debug._inventory

    def delayed(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(debug, "_inventory", delayed)
    errors = []

    def activate():
        try:
            debug.start(identity(), identity(), 60)
        except BusinessError as exc:
            errors.append(exc.code)

    worker = threading.Thread(target=activate)
    worker.start()
    try:
        assert entered.wait(10)
        assert not debug.stop("runtime_shutdown")["active"]
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and errors == ["debug_activation_cancelled"]
    assert not debug.snapshot()["active"]
    with pytest.raises(BusinessError) as error:
        debug.start(identity(), identity(), 60)
    assert error.value.code == "debug_closing"
