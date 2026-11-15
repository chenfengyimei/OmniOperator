"""Test-report diagnostics; never changes test execution, assertions or offline gates."""

from __future__ import annotations

import pytest
from _pytest.runner import CallInfo


def fallback_report(item: pytest.Item, call: CallInfo[None]) -> pytest.TestReport:
    """Build a failed report without source/AST rendering or arbitrary exception formatting."""
    assert call.excinfo is not None
    error = call.excinfo.value
    message = error.args[0] if error.args and type(error.args[0]) is str else "<non-text exception arguments>"
    if len(message) > 4096:
        message = message[:4096] + " [truncated]"
    lines = [
        "failure report MemoryError: source rendering failed; original failure retained.",
        f"Original {type(error).__name__}: {message}",
    ]
    entry = error.__traceback__
    for _ in range(8):
        if entry is None:
            break
        code = entry.tb_frame.f_code
        lines.append(f"  {code.co_filename[:512]}:{entry.tb_lineno} in {code.co_name[:128]}")
        entry = entry.tb_next
    if entry is not None:
        lines.append("  [traceback frames truncated]")
    return pytest.TestReport(
        item.nodeid,
        item.location,
        {key: 1 for key in item.keywords},
        "failed",
        "\n".join(lines),
        call.when,
        [(f"Captured {key} {when}", content) for when, key, content in item._report_sections],
        call.duration,
        call.start,
        call.stop,
        user_properties=item.user_properties,
        report_memory_fallback=True,
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: CallInfo[None]):
    try:
        return (yield)
    except MemoryError:
        if call.excinfo is None or isinstance(call.excinfo.value, pytest.skip.Exception):
            raise
        return fallback_report(item, call)


def pytest_report_teststatus(report: pytest.TestReport, config: pytest.Config):
    if report.failed and not hasattr(config, "workerinput") and not getattr(report, "failure_notice_emitted", False):
        terminal = config.pluginmanager.getplugin("terminalreporter")
        if terminal is not None:
            report.failure_notice_emitted = True
            terminal.write_line(f"FAILURE {report.nodeid} [{report.when}]", red=True)
