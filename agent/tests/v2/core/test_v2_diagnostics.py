"""Diagnostics must preserve context, redact data, remain bounded and expose loss."""

import asyncio
import json
import logging
import sys
import threading
from io import StringIO

import pytest

from omni.adapters.diagnostics import JsonLogSink, LoggingSession, bind_context
from omni.adapters.log_query import LogQuery, LogReader
from omni.adapters.process_diagnostics import ProcessDiagnostics
from omni.domain.contracts import BusinessError


def test_nested_redaction_and_traceback(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)
    sink.redactor.register("EXACT-CREDENTIAL-CANARY")
    try:
        with bind_context(run_id="run-one"):
            try:
                raise ValueError("Authorization: Bearer EXACT-CREDENTIAL-CANARY")
            except ValueError:
                logging.getLogger("omni.v2.test").exception("Failure at https://example.com?code=secret")
        result = sink.redactor.value(
            {"nested": [{"refresh_token": "nested-canary"}], "url": "https://name:pass@example.com/path?token=hidden"}
        )
        assert "nested-canary" not in json.dumps(result)
    finally:
        assert session.close()
    content = (tmp_path / "runtime.jsonl").read_text()
    assert "EXACT-CREDENTIAL-CANARY" not in content and "code=secret" not in content
    entry = json.loads(content)
    assert entry["run_id"] == "run-one" and "ValueError" in entry["exception"]


def test_thread_context_is_propagated_without_leaking_between_requests(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)

    async def scenario():
        with bind_context(run_id="first"):
            await asyncio.to_thread(logging.getLogger("omni.v2.context").info, "first")
        with bind_context(run_id="second"):
            await asyncio.to_thread(logging.getLogger("omni.v2.context").info, "second")
        logging.getLogger("omni.v2.context").info("outside")

    try:
        asyncio.run(scenario())
    finally:
        session.close()
    rows = [json.loads(line) for line in (tmp_path / "runtime.jsonl").read_text().splitlines()]
    assert [row.get("run_id") for row in rows] == ["first", "second", None]


def test_queue_overload_is_observable_and_does_not_block(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build", capacity=1)
    session = LoggingSession(sink)
    entered, release = threading.Event(), threading.Event()
    original = sink._write

    def blocked(payload):
        entered.set()
        assert release.wait(10)
        original(payload)

    sink._write = blocked
    try:
        logger = logging.getLogger("omni.v2.overload")
        logger.info("first")
        assert entered.wait(10)
        logger.info("second")
        logger.info("third")
        assert sink.snapshot()["dropped"] == 1
    finally:
        release.set()
        session.close()


def test_disk_error_remains_visible_without_killing_writer(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)

    def full(_payload):
        raise OSError("simulated disk full")

    sink._write = full
    logging.getLogger("omni.v2.full").error("bounded failure")
    assert session.close()
    assert sink.failures == 1 and sink.last_error == "log_write_failed"


def test_process_hooks_capture_once_restore_and_do_not_record_user_payloads(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)
    previous = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    library = logging.getLogger("external-test-library")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    library.addHandler(handler)

    async def scenario():
        loop = asyncio.get_running_loop()
        old_async = loop.get_exception_handler()
        hooks = ProcessDiagnostics(sink, loop)
        try:
            with pytest.raises(RuntimeError, match="already has an owner"):
                ProcessDiagnostics(sink, loop)
            library.warning("Cookie: library-cookie; full-private-chat-canary")
            late_library = logging.getLogger("late-imported-test-library")
            late_library.addHandler(handler)
            late_library.propagate = False
            hooks.capture((late_library,))
            late_library.error("late-private-chat-canary")
            failure = ValueError("full-private-chat-canary")
            sys.excepthook(type(failure), failure, None)

            def fail_thread():
                raise ValueError("thread-private-chat-canary")

            thread = threading.Thread(target=fail_thread)
            thread.start()
            thread.join(10)
            assert not thread.is_alive()
            loop.call_exception_handler({"message": "private-callback-canary", "exception": failure})

            class BrokenFinalizer:
                def __del__(self):
                    raise RuntimeError("private-finalizer-canary")

            item = BrokenFinalizer()
            del item
            assert hooks.counts == {"process": 1, "thread": 1, "unraisable": 1, "asyncio": 1}
        finally:
            hooks.close()
            hooks.close()
        assert loop.get_exception_handler() is old_async
        assert previous == (sys.excepthook, threading.excepthook, sys.unraisablehook)
        assert handler in library.handlers
        assert handler in late_library.handlers
        late_library.removeHandler(handler)

    try:
        asyncio.run(scenario())
    finally:
        library.removeHandler(handler)
        assert session.close()
    content = (tmp_path / "runtime.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in content.splitlines()]
    assert len(rows) == 6
    assert "canary" not in content and "library-cookie" not in content
    assert {row["event"] for row in rows if row["component"] == "omni.v2.unhandled"} == {
        "unhandled.process",
        "unhandled.thread",
        "unhandled.unraisable",
        "unhandled.asyncio",
    }
    assert stream.getvalue() == ""


def test_log_cursor_survives_rotation_and_restart_and_reports_retention_loss(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="first", build_id="build", segment_bytes=2048, segments=3)
    session = LoggingSession(sink)
    logger = logging.getLogger("omni.v2.cursor")
    try:
        with bind_context(run_id="target-run"):
            logger.warning("first entry")
            logger.warning("second entry")
        sink.queue.join()
        page = LogReader(sink).page(LogQuery(limit=1, run_id="target-run"))
        cursor = page["next_cursor"]
        assert page["items"][0]["message"] == "first entry" and page["has_more"]
    finally:
        assert session.close()
    sink = JsonLogSink(tmp_path, boot_id="second", build_id="build", segment_bytes=2048, segments=3)
    session = LoggingSession(sink)
    try:
        for _ in range(5):
            logger.info("background " + "x" * 400)
        sink.queue.join()
        resumed = LogReader(sink).page(LogQuery(cursor=cursor, run_id="target-run"))
        assert [item["message"] for item in resumed["items"]] == ["second entry"]
        assert resumed["next_cursor"] != cursor
        for _ in range(15):
            logger.info("background " + "x" * 400)
        sink.queue.join()
        with pytest.raises(BusinessError) as error:
            LogReader(sink).page(LogQuery(cursor=cursor))
        assert error.value.code == "log_cursor_expired" and error.value.status == 410
    finally:
        assert session.close()


def test_log_filters_cursor_validation_corruption_and_stderr_mode(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)
    try:
        logger = logging.getLogger("omni.v2.filtered")
        logger.info("excluded")
        with bind_context(run_id="chosen"):
            logger.error("中文诊断")
        sink.queue.join()
        reader = LogReader(sink)
        result = reader.page(
            LogQuery(level="ERROR", run_id="chosen", component=logger.name, since="2020-01-01T00:00:00Z")
        )
        assert [item["message"] for item in result["items"]] == ["中文诊断"]
        assert reader.page(LogQuery(cursor=result["next_cursor"]))["items"] == []
        with pytest.raises(BusinessError, match="malformed"):
            reader.page(LogQuery(cursor="not-a-cursor"))
        with pytest.raises(BusinessError, match="timezone"):
            LogQuery(since="2026-11-02T10:00:00")
        with sink.io_lock, (tmp_path / "runtime.jsonl").open("ab") as stream:
            stream.write(b'{"broken":')
        with pytest.raises(BusinessError) as error:
            reader.page(LogQuery())
        assert error.value.code == "log_record_invalid"
        sink.stderr_only = True
        with pytest.raises(BusinessError) as error:
            reader.page(LogQuery())
        assert error.value.code == "logs_external"
    finally:
        assert session.close()


def test_filtered_log_scan_is_bounded_and_empty_page_can_resume(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)
    try:
        logger = logging.getLogger("omni.v2.scan")
        for _ in range(420):
            logger.info("x" * 3000)
        logger.error("last matching record")
        sink.queue.join()
        reader = LogReader(sink)
        page = reader.page(LogQuery(level="ERROR"))
        assert page["items"] == [] and page["has_more"]
        assert 1_048_576 <= page["scanned_bytes"] <= 1_048_576 + 32768
        page = reader.page(LogQuery(level="ERROR", cursor=page["next_cursor"]))
        assert [row["message"] for row in page["items"]] == ["last matching record"]
        assert not page["has_more"]
        assert sink.dropped == 0
    finally:
        assert session.close()


def test_diagnostic_payload_is_bounded_before_admission_and_invalid_numbers_are_visible(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)
    try:
        logger = logging.getLogger("omni.v2.payload")
        with bind_context(run_id="r" * 100_000):
            logger.info("中文" * 50_000)
        logger.info("invalid metric", extra={"duration_ms": float("nan")})
        sink.queue.join()
        lines = (tmp_path / "runtime.jsonl").read_bytes().splitlines(keepends=True)
        assert len(lines) == 1 and len(lines[0]) <= 32768
        row = json.loads(lines[0])
        assert len(row["run_id"]) == 200
        assert sink.dropped == 1
        value = sink.redactor.value([{"nested": ["x" * 8192] * 100}] * 100)
        assert len(json.dumps(value)) < 40_000
    finally:
        assert session.close()


def test_startup_failure_releases_process_hooks_and_instance_lock(tmp_path, monkeypatch):
    from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
    from omni.composition import instance as composition

    def fail_database(*args, **kwargs):
        raise OSError("injected storage startup failure")

    monkeypatch.setattr(composition, "Database", fail_database)
    previous = (sys.excepthook, threading.excepthook, sys.unraisablehook)

    async def scenario():
        loop = asyncio.get_running_loop()
        old = loop.get_exception_handler()
        with pytest.raises(OSError, match="injected storage"):
            await composition.open_instance(tmp_path, process_diagnostics=True)
        assert loop.get_exception_handler() is old
        assert previous == (sys.excepthook, threading.excepthook, sys.unraisablehook)
        lock = InstanceLock(InstancePaths(tmp_path))
        lock.acquire()
        lock.close()

    asyncio.run(scenario())
