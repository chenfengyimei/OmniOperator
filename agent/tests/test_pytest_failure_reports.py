"""Rendering exhaustion must preserve real failures and allow later tests to execute."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest_plugins = ["pytester"]


def make_case(pytester, phase="call", renderer="MemoryError"):
    pytester.makeconftest(
        f"""
import json
from pathlib import Path
import pytest

def pytest_configure():
    def broken(*args, **kwargs):
        raise {renderer}("injected renderer failure")
    pytest.Function.repr_failure = broken
    pytest.Function._repr_failure_py = broken

def pytest_runtest_logreport(report):
    with Path("reports.jsonl").open("a",encoding="utf-8") as stream:
        stream.write(json.dumps({{
            "nodeid":report.nodeid,"when":report.when,"outcome":report.outcome,
            "fallback":getattr(report,"report_memory_fallback",False),
            "longrepr":str(report.longrepr)
        }})+"\\n")
"""
    )
    pytester.makepyfile(
        test_subject=f"""
import pytest

@pytest.fixture
def phase_failure():
    if {phase!r} == "setup":
        raise AssertionError("ORIGINAL_FAILURE_setup")
    yield
    if {phase!r} == "teardown":
        raise AssertionError("ORIGINAL_FAILURE_teardown")

def test_original(phase_failure):
    if {phase!r} == "call":
        raise AssertionError("ORIGINAL_FAILURE_call")

def test_after():
    assert True
"""
    )


def records(pytester):
    return [json.loads(line) for line in (pytester.path / "reports.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_rendering_memory_error_reports_original_phase_and_runs_following_test(pytester, phase):
    make_case(pytester, phase)
    result = pytester.runpytest_subprocess("-p", "failure_reports", "-q", "-n", "0")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    reports = records(pytester)
    failed = [report for report in reports if report["outcome"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["nodeid"] == "test_subject.py::test_original" and failed[0]["when"] == phase
    assert failed[0]["fallback"] is True
    assert "AssertionError" in failed[0]["longrepr"] and f"ORIGINAL_FAILURE_{phase}" in failed[0]["longrepr"]
    assert "MemoryError" in failed[0]["longrepr"]
    assert any(
        report["nodeid"] == "test_subject.py::test_after" and report["when"] == "call" and report["outcome"] == "passed"
        for report in reports
    )
    assert "INTERNALERROR" not in result.stdout.str()


def test_xdist_sends_failure_and_finishes_every_node_after_renderer_memory_error(pytester):
    make_case(pytester)
    pytester.makepyfile("def test_other_one(): assert True\ndef test_other_two(): assert True")
    result = pytester.runpytest_subprocess("-p", "failure_reports", "-q", "-n", "2", "--dist=loadfile")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(failed=1, passed=3)
    assert "INTERNALERROR" not in result.stdout.str()
    assert result.stdout.str().count("FAILURE test_subject.py::test_original [call]") == 1


def test_non_memory_renderer_errors_remain_internal_errors(pytester):
    make_case(pytester, renderer="RuntimeError")
    result = pytester.runpytest_subprocess("-p", "failure_reports", "-q", "-n", "0")
    assert result.ret == pytest.ExitCode.INTERNAL_ERROR
    assert "RuntimeError: injected renderer failure" in result.stdout.str()


@pytest.mark.parametrize("original_outcome", ["pass", "skip"])
def test_original_pass_or_skip_cannot_be_fabricated_as_failure_to_hide_reporter_error(pytester, original_outcome):
    pytester.makeconftest(
        """
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(item,call):
    if call.when == "call":
        raise MemoryError("no original failed call")
"""
    )
    pytester.makepyfile(
        "import pytest\ndef test_original(): "
        + ("assert True" if original_outcome == "pass" else 'pytest.skip("original skip")')
    )
    result = pytester.runpytest_subprocess("-p", "failure_reports", "-q", "-n", "0")
    assert result.ret == pytest.ExitCode.INTERNAL_ERROR
    assert "MemoryError: no original failed call" in result.stdout.str()


def test_fallback_preserves_metadata_and_bounds_diagnostic_without_calling_custom_string():
    from failure_reports import fallback_report

    class Hostile:
        def __str__(self):
            raise AssertionError("Do not format arbitrary exception arguments")

        __repr__ = __str__

    item = SimpleNamespace(
        nodeid="subject.py::test_original",
        location=("subject.py", 7, "test_original"),
        keywords={"marker": True},
        _report_sections=[("call", "stdout", "captured output")],
        user_properties=[("property", "value")],
    )
    for arguments in (("ORIGINAL" + "x" * 10_000, Hostile()), (Hostile(),)):
        try:
            raise AssertionError(*arguments)
        except AssertionError as caught:
            excinfo = pytest.ExceptionInfo.from_exception(caught)
        call = SimpleNamespace(excinfo=excinfo, when="call", duration=1.25, start=10.0, stop=11.25)
        report = fallback_report(item, call)
        assert report.failed and report.nodeid == item.nodeid and report.location == item.location
        assert (report.duration, report.start, report.stop, report.when) == (1.25, 10.0, 11.25, "call")
        assert report.sections == [("Captured stdout call", "captured output")]
        assert report.user_properties == [("property", "value")]
        if type(arguments[0]) is str:
            assert "ORIGINAL" in report.longrepr and "truncated" in report.longrepr
        else:
            assert "<non-text exception arguments>" in report.longrepr
        assert len(report.longrepr) < 5000 and report.report_memory_fallback is True
        json.dumps(report.__dict__)


def test_ordinary_reports_keep_outcomes_and_emit_failure_identity_once(pytester):
    pytester.makepyfile(
        """
def test_bad():
    assert False, "ordinary failure"

def test_after():
    assert True
"""
    )
    result = pytester.runpytest_subprocess("-p", "failure_reports", "-q", "-n", "0")
    result.assert_outcomes(failed=1, passed=1)
    assert (
        result.stdout.str().count(
            "FAILURE test_ordinary_reports_keep_outcomes_and_emit_failure_identity_once.py::test_bad [call]"
        )
        == 1
    )
    assert "failure report MemoryError" not in result.stdout.str()
