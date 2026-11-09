"""Operational data stays bounded, preserves unknown costs and excludes business bodies."""

import asyncio
import io
import json
import logging
import os
import threading
import time
import uuid
import zipfile

import pytest

from omni.adapters.diagnostics import JsonLogSink, Redactor
from omni.adapters.log_query import MAX_SCAN, LogReader
from omni.adapters.metrics import BOUNDS, Metrics
from omni.adapters.persistence.database import Database
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


def test_secret_registry_is_bounded_without_evicting_old_credentials():
    redactor = Redactor()
    for index in range(256):
        redactor.register(f"exact-credential-{index:03d}")
    redactor.register("exact-credential-000")
    with pytest.raises(BusinessError) as failure:
        redactor.register("unprotected-new-credential")
    assert failure.value.code == "redaction_capacity"
    assert redactor.text("exact-credential-000") == "[redacted]"
    assert redactor.text("x" * 262145) == "[text_limit]"


def test_idle_log_maintenance_expires_owned_segments_and_reports_expired_cursor(tmp_path):
    from omni.adapters.log_query import LogQuery

    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    try:
        row = {"record_id": str(uuid.uuid4()), "timestamp": "2026-11-04T00:00:00+00:00"}
        current = tmp_path / "runtime.jsonl"
        current.write_text(json.dumps(row) + "\n", encoding="utf-8")
        cursor = LogReader(sink).page(LogQuery())["next_cursor"]
        unrelated = tmp_path / "owner-notes.txt"
        unrelated.write_text("must remain", encoding="utf-8")
        old = time.time() - 15 * 86400
        os.utime(current, (old, old))
        os.utime(unrelated, (old, old))
        assert sink.maintenance()["removed_files"] == 1
        assert unrelated.read_text() == "must remain"
        with pytest.raises(BusinessError) as failure:
            LogReader(sink).page(LogQuery(cursor=cursor))
        assert failure.value.code == "log_cursor_expired"
    finally:
        assert sink.shutdown()


def test_metrics_have_fixed_cardinality_consistent_buckets_and_finite_values():
    metrics = Metrics()
    for i in range(1000):
        assert not metrics.observe("run-" + str(i), "ok", 1.0)
    assert not metrics.observe("model", "ok", float("nan"))
    assert not metrics.observe("model", "ok", -1)
    assert not metrics.observe("model", "ok", True)
    assert metrics.observe("model", "ok", 0.02)
    assert metrics.observe("model", "ok", 2.0)
    data = metrics.snapshot()
    assert len(data["series"]) == 1 and data["dropped"] == 1003
    row = data["series"][0]
    assert row["count"] == 2 and row["sum"] == 2.02
    counts = [bucket["count"] for bucket in row["buckets"]]
    assert counts == sorted(counts) and counts[-1] == 2
    assert len(counts) == len(BOUNDS) + 1
    assert "run-999" not in json.dumps(data, allow_nan=False)


def test_runtime_counts_and_default_bundle_do_not_export_private_content(tmp_path):
    canary = "私密正文与凭据必须留在实例中"

    async def scenario():
        paths = InstancePaths.resolve(tmp_path)
        paths.initialize()
        db = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
        await db.write(lambda u: u.initialize())
        agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]
        turn = await db.write(lambda u: u.accept_turn(agent, None, "fixture", canary, {}))
        await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
        await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 50, 100, "private-model-name"))
        await db.write(lambda u: u.recover())
        await db.close()
        instance = await open_instance(tmp_path, capabilities=())
        try:
            for relative in ("private/vault/fixture.secret", "browser/profile/Cookies"):
                path = tmp_path / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(canary, encoding="utf-8")
            logging.getLogger("omni.v2.operations_test").warning(
                canary, extra={"event": "fixture.correlation", "error_code": "fixture_error"}
            )
            instance._logging.sink.queue.join()
            status = await instance.operations.status()
            assert status["persisted"]["model_calls"]["unknown"] == 1
            assert status["persisted"]["money"][0]["reserved_micro"] == 50
            assert status["persisted"]["unknown_cost_is_zero"] is False
            assert status["runtime"]["ready"] is True
            assert "private-model-name" not in json.dumps(status)
            assert turn["session_id"] not in json.dumps(status)
            payload = await instance.operations.export()
            assert len(payload) <= 2 * 1024 * 1024
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                assert set(archive.namelist()) == {"summary.json", "correlations.jsonl", "README.txt"}
                content = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
                assert canary not in content and str(tmp_path) not in content
                assert "private-model-name" not in content
                assert "fixture.correlation" in content and "fixture_error" in content
                assert "omni.db-wal" in content
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_recent_log_excerpt_is_bounded_and_reports_corrupt_and_truncated_content(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id=str(uuid.uuid4()), build_id="a" * 64)
    try:
        row = {
            "record_id": str(uuid.uuid4()),
            "timestamp": "2026-11-04T00:00:00+00:00",
            "level": "INFO",
            "event": "fixture.final",
            "component": "omni.v2.fixture",
            "message": "private-long-log" + "x" * 512,
        }
        line = (json.dumps(row) + "\n").encode()
        with sink.io_lock:
            (tmp_path / "runtime.jsonl").write_bytes(line * (MAX_SCAN // len(line) + 10) + b"broken\n" + line)
        report = LogReader(sink).tail()
        assert report["scanned_bytes"] <= MAX_SCAN and report["truncated"]
        assert report["invalid_records"] == 1 and len(report["items"]) == 500
        assert report["items"][-1]["event"] == "fixture.final"
    finally:
        assert sink.shutdown()


def test_tiny_invalid_log_records_have_a_separate_scan_bound(tmp_path):
    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    try:
        (tmp_path / "runtime.jsonl").write_bytes(b"{}\n" * 20000)
        report = LogReader(sink).tail()
        assert report["scanned_records"] == report["invalid_records"] == 10000
        assert report["truncated"] and report["items"] == []
        assert report["scanned_bytes"] <= MAX_SCAN
    finally:
        assert sink.shutdown()


def test_owned_diagnostic_threads_propagate_and_reset_context(tmp_path):
    from omni.adapters.diagnostic_bundle import DiagnosticFiles
    from omni.adapters.diagnostics import LoggingSession, bind_context

    paths = InstancePaths.resolve(tmp_path)
    paths.initialize()
    sink = JsonLogSink(paths.logs, boot_id="boot", build_id="build")
    logging_session = LoggingSession(sink)

    async def scenario():
        files = DiagnosticFiles(paths, sink)
        try:
            for identifier in ("first", "second"):
                with bind_context(run_id=identifier):
                    await files._run(lambda: logging.getLogger("omni.v2.diagnostic_worker").info("worker"))
            await files._run(lambda: logging.getLogger("omni.v2.diagnostic_worker").info("outside"))
        finally:
            assert await files.close()

    try:
        asyncio.run(scenario())
    finally:
        assert logging_session.close()
    rows = [json.loads(line) for line in (paths.logs / "runtime.jsonl").read_text().splitlines()]
    assert [row.get("run_id") for row in rows] == ["first", "second", None]


def test_cancelled_diagnostic_stays_charged_and_close_retains_database(tmp_path, monkeypatch):
    async def scenario():
        instance = await open_instance(tmp_path, capabilities=())
        entered, release = threading.Event(), threading.Event()
        files = instance.operations._diagnostic
        original = files._describe

        def delayed():
            entered.set()
            assert release.wait(10)
            return original()

        monkeypatch.setattr(files, "_describe", delayed)
        callers = [asyncio.create_task(instance.operations.status()) for _ in range(4)]
        try:
            async with asyncio.timeout(10):
                while not entered.is_set() or len(instance.operations._pending) != 4:
                    await asyncio.sleep(0.01)
            callers[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await callers[0]
            assert len(instance.operations._pending) == 4
            with pytest.raises(BusinessError, match="busy"):
                await instance.operations.status()
            result = await instance.close(0.02)
            assert not result["complete"] and not instance._database.metrics["closed"]
            release.set()
            await asyncio.gather(*callers, return_exceptions=True)
            assert (await instance.close(10))["complete"]
        finally:
            release.set()
            await asyncio.gather(*callers, return_exceptions=True)
            await instance.close()

    asyncio.run(scenario())


def test_failed_turn_and_unknown_model_cost_are_not_successful_metrics_or_trace_payloads(tmp_path):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from v2_configuration_helpers import model_instance

    class Model:
        async def complete(self, *args):
            raise RuntimeError("private-model-exception-canary")

    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        exporter = InMemorySpanExporter()
        instance._observation.provider.add_span_processor(SimpleSpanProcessor(exporter))
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "failure", "private request")
            async with asyncio.timeout(10):
                while (await instance.application.get_run(agent, receipt["run_id"]))["state"] not in {
                    "failed",
                    "interrupted",
                }:
                    await asyncio.sleep(0.01)
            async with asyncio.timeout(10):
                while instance.application._active:
                    await asyncio.sleep(0.01)
            report = await instance.operations.status()
            labels = [entry["labels"] for entry in report["measurements"]["series"]]
            assert {"operation": "turn", "outcome": "error"} in labels
            assert {"operation": "model", "outcome": "unknown"} in labels
            assert {"operation": "turn", "outcome": "ok"} not in labels
            assert report["persisted"]["model_calls"]["unknown"] == 1
            assert report["persisted"]["money"][0]["reserved_micro"] > 0
            spans = exporter.get_finished_spans()
            assert spans and not any(span.events for span in spans)
            assert "private-model-exception-canary" not in "\n".join(span.to_json() for span in spans)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_late_provider_receipt_keeps_delivery_metric_unknown(tmp_path):
    from v2_configuration_helpers import model_instance
    from v2_delivery_helpers import FakePort

    from omni.domain.contracts import ModelReply
    from omni.domain.delivery import DeliveryResult

    class Model:
        async def complete(self, *args):
            return ModelReply("generated output", cost_micro=0)

    class Port(FakePort):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send(self, message):
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    pass
            return DeliveryResult("sent", message.fingerprint, receipt_id="late")

    async def scenario():
        port = Port()
        instance = await open_instance(tmp_path, model=Model(), capabilities=(), delivery_ports={"qq": port})
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "turn", "generate")
            async with asyncio.timeout(10):
                while (await instance.application.get_run(agent, receipt["run_id"]))["state"] != "succeeded":
                    await asyncio.sleep(0.01)
            message = (await instance.application.messages(agent, receipt["session_id"]))[-1]
            config = await instance.configurations.create(
                agent,
                "channel",
                "Fixture",
                True,
                {"driver": "qq", "account": "account-one", "credential_ref": "fixture/key"},
            )
            row = await instance.deliveries.create(agent, message["id"], config["id"], "target", "intent")
            await instance.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            await asyncio.wait_for(port.started.wait(), 10)
            await instance.deliveries.cancel(agent, row["id"])
            port.release.set()
            async with asyncio.timeout(10):
                while instance.deliveries._active:
                    await asyncio.sleep(0.01)
            labels = [entry["labels"] for entry in (await instance.operations.status())["measurements"]["series"]]
            assert {"operation": "delivery", "outcome": "unknown"} in labels
            assert {"operation": "delivery", "outcome": "ok"} not in labels
            assert (await instance.deliveries.get(agent, row["id"]))["state"] == "unknown"
        finally:
            port.release.set()
            await instance.close()

    asyncio.run(scenario())


def test_unclassified_write_exception_is_an_unknown_tool_measurement(tmp_path):
    from v2_configuration_helpers import model_instance

    from omni.application.engine import Capability
    from omni.domain.contracts import Invocation, ModelReply, ToolSpec

    class Writer:
        async def invoke(self, *args, **kwargs):
            raise RuntimeError("private write may already have happened")

        async def close(self, timeout_s):
            return True

    class Model:
        async def complete(self, *args):
            return ModelReply("", (Invocation(str(uuid.uuid4()), "write", {}),), cost_micro=0)

    async def scenario():
        instance = await open_instance(
            tmp_path,
            model=Model(),
            capabilities=(Capability(ToolSpec("write", "fixture", {"type": "object"}, writes=True), Writer()),),
        )
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            receipt = await app.submit_turn(agent, None, "write", "write")
            async with asyncio.timeout(10):
                while not (approvals := await app.approvals(agent)):
                    await asyncio.sleep(0.01)
            approval = approvals[0]
            await app.approve(agent, approval["id"], True, approval["policy_revision"])
            async with asyncio.timeout(10):
                while app._active:
                    await asyncio.sleep(0.01)
            assert (await app.query("actions", agent))[0]["state"] == "unknown"
            assert (await app.get_run(agent, receipt["run_id"]))["state"] == "failed"
            labels = [row["labels"] for row in (await instance.operations.status())["measurements"]["series"]]
            assert {"operation": "tool", "outcome": "unknown"} in labels
            assert {"operation": "tool", "outcome": "ok"} not in labels
        finally:
            await instance.close()

    asyncio.run(scenario())
