"""Diagnostics must preserve context, redact data, remain bounded and expose loss."""

import asyncio
import json
import logging
import threading

from omni.adapters.diagnostics import JsonLogSink, LoggingSession, bind_context


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
