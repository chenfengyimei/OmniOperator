"""Interrupted candidate removal remains explicitly owned and can finish without deleting instance data."""

import json
import os
from pathlib import Path

import pytest
from omni_test_support.v2.wheel_uninstall import active_candidate

from omni.adapters.platform.releases import wheel_uninstall as uninstall
from omni.adapters.platform.releases.wheel_status import installation_status
from omni.adapters.platform.runtime.permissions import private_path
from omni.domain.contracts import BusinessError


def test_failed_removal_can_resume_after_deactivation_without_data_loss(tmp_path, monkeypatch):
    paths, digest = active_candidate(tmp_path)
    database = paths.database.read_bytes()
    remove = uninstall._remove_stage

    def denied(stage, updates):
        raise PermissionError("injected temporary file lock")

    monkeypatch.setattr(uninstall, "_remove_stage", denied)
    with pytest.raises(PermissionError):
        uninstall.uninstall_candidate(paths, digest)
    assert not (paths.home / "runtime/current.json").exists()
    assert paths.database.read_bytes() == database
    pending = installation_status(paths)
    assert pending["runtime"]["cleanup_pending"] and pending["runtime"]["cleanup_candidate"] == digest
    assert pending["candidates"][0]["next_step"] == "uninstall"
    monkeypatch.setattr(uninstall, "_remove_stage", remove)
    result = uninstall.uninstall_candidate(paths, digest)
    assert result["data_kept"] and result["removed"]
    assert Path(result["backup_kept"]).is_dir()
    assert not (paths.home / "updates" / digest[:16]).exists()
    assert paths.database.read_bytes() == database
    assert installation_status(paths)["runtime"]["cleanup_pending"] is False
    repeated = uninstall.uninstall_candidate(paths, digest)
    assert repeated["data_kept"] and repeated["removed"] is False


def test_resume_does_not_remove_a_candidate_after_another_is_selected(tmp_path, monkeypatch):
    paths, digest = active_candidate(tmp_path)
    pointer = paths.home / "runtime/current.json"
    previous = json.loads(pointer.read_text(encoding="utf-8"))
    monkeypatch.setattr(uninstall, "_remove_stage", lambda *args: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        uninstall.uninstall_candidate(paths, digest)
    pointer.write_text(json.dumps({**previous, "wheel_sha256": "b" * 64}), encoding="utf-8")
    with pytest.raises(BusinessError) as caught:
        uninstall.uninstall_candidate(paths, digest)
    assert caught.value.code == "uninstall_not_active"
    assert (paths.home / "updates" / digest[:16]).is_dir()


def test_a_stage_without_an_uninstall_receipt_is_not_authorized_for_resume(tmp_path):
    paths, digest = active_candidate(tmp_path)
    uninstall.uninstall_candidate(paths)
    with pytest.raises(BusinessError) as caught:
        uninstall.uninstall_candidate(paths, digest)
    assert caught.value.code == "uninstall_not_active"


@pytest.mark.parametrize(
    "content", ["[]", "bad json", '{"format":"other"}', '{"format":"omni-uninstall-1","phase":[]}']
)
def test_malformed_removal_receipt_fails_closed(tmp_path, content):
    paths, digest = active_candidate(tmp_path)
    receipt = paths.home / "runtime/uninstall.json"
    receipt.write_text(content, encoding="utf-8")
    private_path(receipt)
    with pytest.raises(BusinessError) as caught:
        uninstall.uninstall_candidate(paths, digest)
    assert caught.value.code == "uninstall_receipt_invalid"
    assert (paths.home / "runtime/current.json").is_file()
    assert (paths.home / "updates" / digest[:16]).is_dir()


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_windows_transient_removal_failure_retries_the_same_contained_stage(tmp_path, monkeypatch, winerror):
    if os.name != "nt":
        return
    updates = tmp_path / "updates"
    stage = updates / "candidate"
    stage.mkdir(parents=True)
    (stage / "proof").write_bytes(b"owned")
    original, calls, waits = uninstall.shutil.rmtree, [], []

    def remove(path):
        calls.append(path)
        if len(calls) == 1:
            error = PermissionError("injected sharing failure")
            error.winerror = winerror
            raise error
        return original(path)

    monkeypatch.setattr(uninstall.shutil, "rmtree", remove)
    monkeypatch.setattr(uninstall.time, "sleep", waits.append)
    uninstall._remove_stage(stage, updates)
    assert calls == [stage, stage] and waits == [0.1] and not stage.exists()


def test_windows_permanent_removal_failure_is_bounded_and_preserved(tmp_path, monkeypatch):
    if os.name != "nt":
        return
    stage = tmp_path / "updates/candidate"
    stage.mkdir(parents=True)
    calls, waits = [], []
    error = PermissionError("still locked")
    error.winerror = 5

    def remove(path):
        calls.append(path)
        raise error

    monkeypatch.setattr(uninstall.shutil, "rmtree", remove)
    monkeypatch.setattr(uninstall.time, "sleep", waits.append)
    with pytest.raises(PermissionError) as caught:
        uninstall._remove_stage(stage, stage.parent)
    assert caught.value is error and len(calls) == 4 and waits == [0.1, 0.25, 0.5]
    assert stage.is_dir()


def test_unrelated_removal_error_is_not_retried_or_discarded(tmp_path, monkeypatch):
    stage = tmp_path / "updates/candidate"
    stage.mkdir(parents=True)
    calls = []

    def remove(path):
        calls.append(path)
        raise OSError("disk failure")

    monkeypatch.setattr(uninstall.shutil, "rmtree", remove)
    with pytest.raises(OSError, match="disk failure"):
        uninstall._remove_stage(stage, stage.parent)
    assert calls == [stage]


@pytest.mark.parametrize("purge", [False, True])
def test_running_candidate_cannot_delete_its_own_interpreter(tmp_path, monkeypatch, purge):
    paths, digest = active_candidate(tmp_path)
    python = paths.home / "updates" / digest[:16] / "venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"not executed")
    monkeypatch.setattr(uninstall.sys, "executable", str(python))
    with pytest.raises(BusinessError) as caught:
        uninstall.uninstall_candidate(paths, "" if purge else digest, purge=purge)
    assert caught.value.code == "uninstall_external_manager_required"
    assert (paths.home / "runtime/current.json").is_file() and paths.database.is_file()


def test_windows_persistent_lock_reports_an_actionable_resumable_failure(tmp_path, monkeypatch):
    if os.name != "nt":
        return
    paths, digest = active_candidate(tmp_path)
    error = PermissionError("injected persistent lock")
    error.winerror = 32
    monkeypatch.setattr(uninstall, "_remove_stage", lambda *args: (_ for _ in ()).throw(error))
    with pytest.raises(BusinessError) as caught:
        uninstall.uninstall_candidate(paths, digest)
    assert caught.value.code == "uninstall_cleanup_pending" and caught.value.retryable
    assert caught.value.status == 409 and (paths.home / "updates" / digest[:16]).is_dir()
    assert installation_status(paths)["runtime"]["cleanup_pending"]
