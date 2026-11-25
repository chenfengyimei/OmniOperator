"""Isolation enforcement cannot be defeated by caught exceptions or subprocesses."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from offline_guard import OfflineViolation, filesystem_auditor


def test_file_auditor_rejects_mutation_and_sqlite_in_protected_roots(tmp_path):
    protected = tmp_path / "production"
    protected.mkdir()
    violations = []

    def refuse(kind):
        violations.append(kind)
        raise OfflineViolation(kind)

    guard = filesystem_auditor([protected], refuse)
    for event, args in [
        ("open", (str(protected / "test"), "w", os.O_WRONLY | os.O_CREAT)),
        ("sqlite3.connect", ((protected / "memory.db").as_uri() + "?mode=ro",)),
        ("os.rename", (str(tmp_path / "source"), str(protected / "target"), -1, -1)),
        ("os.mkdir", (str(protected / "nested"), 0o700, -1)),
    ]:
        with pytest.raises(OfflineViolation):
            guard(event, args)
    guard("open", (str(protected / "test"), "r", os.O_RDONLY))
    guard("open", (str(tmp_path / "isolated"), "w", os.O_WRONLY | os.O_CREAT))
    assert len(violations) == 4
    assert not list(protected.iterdir())


@pytest.mark.skipif(os.rmdir not in os.supports_dir_fd, reason="POSIX directory descriptors")
def test_directory_relative_cleanup_respects_the_descriptor_base(tmp_path, monkeypatch):
    protected = tmp_path / "data"
    protected.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "data").mkdir()
    monkeypatch.chdir(tmp_path)

    def refuse(kind):
        raise OfflineViolation(kind)

    guard = filesystem_auditor([protected], refuse)
    isolated_fd = os.open(scratch, os.O_RDONLY)
    protected_fd = os.open(protected, os.O_RDONLY)
    try:
        guard("os.rmdir", ("data", isolated_fd))
        with pytest.raises(OfflineViolation, match="filesystem.real_instance"):
            guard("os.rmdir", ("nested", protected_fd))
        with pytest.raises(OfflineViolation, match="filesystem.real_instance"):
            guard("os.rename", ("data", "nested", isolated_fd, protected_fd))
    finally:
        os.close(protected_fd)
        os.close(isolated_fd)


def test_child_cannot_open_real_instance_even_when_exception_is_caught(tmp_path):
    protected = tmp_path / "fake-production"
    protected.mkdir()
    source = protected / "memory.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
    env = dict(
        os.environ,
        OMNI_TEST_VIOLATIONS=str(tmp_path / "expected-denial.txt"),
        OMNI_TEST_EXTRA_PROTECTED_ROOT=str(protected),
    )
    code = """
import sqlite3,sys
from offline_guard import OfflineViolation
try:
    sqlite3.connect(sys.argv[1])
except OfflineViolation:
    print('guarded')
except sqlite3.Error:
    raise AssertionError('filesystem guard was bypassed')
else:
    raise AssertionError('filesystem guard was bypassed')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, source.as_uri() + "?mode=rw"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and "guarded" in result.stdout, result.stderr
    assert Path(env["OMNI_TEST_VIOLATIONS"]).read_text().strip() == "filesystem.real_instance"
