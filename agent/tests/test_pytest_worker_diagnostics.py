"""Persistent diagnostics survive owned worker exits without changing test results."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def events(directory: Path):
    return [
        json.loads(line) for path in directory.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()
    ]


def run(pytester, *args):
    return pytester.runpytest_subprocess("-p", "worker_diagnostics", "-p", "failure_reports", "-q", "-n", "0", *args)


def test_disabled_diagnostics_never_initializes_io_or_memory_sampling(pytester):
    pytester.makeconftest("""
import pytest
import worker_diagnostics
@pytest.hookimpl(tryfirst=True)
def pytest_configure():
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled diagnostics must not initialize")
    worker_diagnostics.Diagnostics = forbidden
    worker_diagnostics.system_commit = forbidden
""")
    pytester.makepyfile("def test_pass(): assert True")
    run(pytester).assert_outcomes(passed=1)
    assert not list(pytester.path.glob("*.jsonl"))


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_phase_failure_identity_outcome_and_following_case_are_preserved(pytester, phase):
    pytester.makepyfile(
        test_subject=f"""
import pytest
@pytest.fixture
def boundary():
    if {phase!r} == "setup": raise AssertionError("SECRET_PAYLOAD")
    yield
    if {phase!r} == "teardown": raise AssertionError("SECRET_PAYLOAD")
def test_original(boundary):
    if {phase!r} == "call": raise AssertionError("SECRET_PAYLOAD")
def test_after(): assert True
"""
    )
    directory = pytester.path / "diagnostics"
    result = run(pytester, "--worker-diagnostics", str(directory))
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    records = events(directory)
    bad = [r for r in records if r["event"] == "report" and r["outcome"] == "failed"]
    assert len(bad) == 1 and bad[0]["nodeid"] == "test_subject.py::test_original" and bad[0]["when"] == phase
    assert any(
        r["event"] == "report"
        and r["nodeid"] == "test_subject.py::test_after"
        and r["when"] == "call"
        and r["outcome"] == "passed"
        for r in records
    )
    assert any(r["event"] == "phase_start" and r["nodeid"] == bad[0]["nodeid"] and r["when"] == phase for r in records)
    assert all("SECRET_PAYLOAD" not in p.read_text(encoding="utf-8") for p in directory.iterdir())
    assert records[-1]["event"] == "session_finish" and records[-1]["diagnostic_errors"] == 0
    if sys.platform == "win32":
        assert records[0]["system_commit"]["CommitTotal"] > 0
        assert records[0]["system_commit"]["CommitLimit"] > 0


@pytest.mark.parametrize("code", [17, 0xC0000005 if sys.platform == "win32" else 23])
def test_abrupt_exit_records_code_and_last_phase_and_replacement_finishes(pytester, code):
    pytester.makeconftest("""
from crash_safe_scheduler import CrashSafeLoadFileScheduling
def pytest_xdist_make_scheduler(config, log):
    return CrashSafeLoadFileScheduling(config, log)
""")
    pytester.makepyfile(
        test_subject=f"""
import os, sys
from pathlib import Path
def test_crash():
    marker = Path("once")
    if not marker.exists():
        marker.touch()
        if sys.platform == "win32" and {code} > 0x7fffffff:
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL("kernel32").TerminateProcess
            api.argtypes = [wintypes.HANDLE, wintypes.UINT]
            api.restype = wintypes.BOOL
            api(wintypes.HANDLE(-1), {code})
        os._exit({code})
def test_after(): assert True
"""
    )
    pytester.makepyfile(test_other="def test_other(): assert True")
    directory = pytester.path / "diagnostics"
    result = run(
        pytester, "-n", "1", "--dist=loadfile", "--max-worker-restart=1", "--worker-diagnostics", str(directory)
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(failed=1, passed=3)
    records = events(directory)
    exit_events = [r for r in records if r["event"] == "worker_exit" and r["returncode"] == code]
    assert exit_events
    worker = exit_events[0]["worker_id"]
    journal = next(directory.glob(f"{worker}-*.jsonl"))
    last = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "phase_start" and last["nodeid"] == "test_subject.py::test_crash" and last["when"] == "call"
    assert last["pid"] > 0 and list(directory.glob(f"{worker}-*.fault.txt"))
    if sys.platform == "win32":
        assert exit_events[0]["windows_status_hex"] == f"0x{code:08x}"
    assert "INTERNALERROR" not in result.stdout.str()


def test_existing_pytest_fault_fd_persists_stack_and_timeout_configuration(pytester):
    pytester.makepyfile(
        test_subject="""
import faulthandler
from _pytest.faulthandler import fault_handler_stderr_fd_key
def test_persistent_stack(request):
    assert faulthandler.is_enabled()
    assert request.config.getini("faulthandler_timeout") == "12.5"
    assert not request.config.getini("faulthandler_exit_on_timeout")
    faulthandler.dump_traceback(file=request.config.stash[fault_handler_stderr_fd_key])
"""
    )
    directory = pytester.path / "diagnostics"
    run(pytester, "--worker-diagnostics", str(directory), "-o", "faulthandler_timeout=12.5").assert_outcomes(passed=1)
    assert "test_persistent_stack" in next(directory.glob("*.fault.txt")).read_text(encoding="utf-8")


def test_existing_directory_is_rejected_without_overwriting_files(pytester):
    pytester.makepyfile("def test_pass(): assert True")
    directory = pytester.path / "diagnostics"
    directory.mkdir()
    marker = directory / "marker"
    marker.write_text("preserve", encoding="utf-8")
    result = run(pytester, "--worker-diagnostics", str(directory))
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert marker.read_text(encoding="utf-8") == "preserve" and list(directory.iterdir()) == [marker]


def test_missing_builtin_fault_handler_is_reported_before_directory_creation(pytester):
    pytester.makepyfile("def test_pass(): assert True")
    directory = pytester.path / "diagnostics"
    result = run(pytester, "-p", "no:faulthandler", "--worker-diagnostics", str(directory))
    assert result.ret == pytest.ExitCode.USAGE_ERROR and not directory.exists()


def test_memory_sampling_error_is_visible_and_original_failure_still_fails(pytester):
    pytester.makeconftest("""
import pytest, worker_diagnostics
@pytest.hookimpl(tryfirst=True)
def pytest_configure():
    def unavailable(): raise MemoryError("injected sampling failure")
    worker_diagnostics.system_commit = unavailable
""")
    pytester.makepyfile("def test_original(): assert False\ndef test_after(): assert True")
    directory = pytester.path / "diagnostics"
    result = run(pytester, "--worker-diagnostics", str(directory))
    result.assert_outcomes(failed=1, passed=1)
    assert "Worker diagnostics incomplete" in result.stdout.str()
    assert any(r.get("system_commit_unavailable") for r in events(directory))


def test_capped_journal_marks_data_loss_and_preserves_failure_and_following_case(pytester):
    pytester.makeconftest("""
import pytest, worker_diagnostics
@pytest.hookimpl(tryfirst=True)
def pytest_configure(): worker_diagnostics._limit = 900
""")
    pytester.makepyfile("def test_original(): assert False\ndef test_after(): assert True")
    directory = pytester.path / "diagnostics"
    result = run(pytester, "--worker-diagnostics", str(directory))
    result.assert_outcomes(failed=1, passed=1)
    assert "Worker diagnostics incomplete" in result.stdout.str()
    journal = next(directory.glob("*.jsonl"))
    assert journal.stat().st_size <= 900 and events(directory)[-1]["event"] == "journal_limit"
