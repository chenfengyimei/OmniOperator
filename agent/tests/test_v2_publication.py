"""Exclusive publication retry boundaries using injected Win32 calls, without overriding an existing directory."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from omni.adapters.platform import publication as p


def inject(monkeypatch, callback):
    monkeypatch.setattr(p.sys, "platform", "win32")

    class Move:
        def __call__(self, source, target, flags):
            assert flags == 0x8  # WRITE_THROUGH; no REPLACE_EXISTING or COPY_ALLOWED.
            return callback(source, target)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(MoveFileExW=Move()), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 32, raising=False)


def test_short_sharing_failure_retries_same_source_and_publishes_once(tmp_path, monkeypatch):
    source, target = tmp_path / "partial", tmp_path / "final"
    source.mkdir()
    (source / "file").write_bytes(b"reviewed")
    calls = []

    def move(old, new):
        calls.append((old, new))
        if len(calls) == 1:
            return False
        source.rename(target)
        return True

    inject(monkeypatch, move)
    p._windows_move(source, target)
    assert len(calls) == 2 and not source.exists() and (target / "file").read_bytes() == b"reviewed"


@pytest.mark.parametrize("change", ["target", "source"])
def test_sharing_error_never_retries_changed_identity_or_new_destination(tmp_path, monkeypatch, change):
    source, target = tmp_path / "partial", tmp_path / "final"
    source.mkdir()
    calls = []

    def move(old, new):
        calls.append((old, new))
        if change == "target":
            target.mkdir()
            (target / "foreign").write_bytes(b"preserve")
        else:
            source.rename(tmp_path / "original")
            source.mkdir()
        return False

    inject(monkeypatch, move)
    with pytest.raises(OSError, match="changed"):
        p._windows_move(source, target)
    assert len(calls) == 1 and source.is_dir()
    if change == "target":
        assert (target / "foreign").read_bytes() == b"preserve"


def test_persistent_sharing_failure_is_bounded_and_preserves_partial(tmp_path, monkeypatch):
    source, target = tmp_path / "partial", tmp_path / "final"
    source.mkdir()
    times = iter((0, 0.2, 0.3, 0.4, 1.1))
    monkeypatch.setattr(p, "time", SimpleNamespace(monotonic=lambda: next(times, 2), sleep=lambda value: None))
    inject(monkeypatch, lambda *args: False)
    with pytest.raises(OSError, match="failed"):
        p._windows_move(source, target)
    assert source.is_dir() and not target.exists()
