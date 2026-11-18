"""Fixed macOS bundle resolution and cross-platform contracts; no macOS APIs or real desktop."""

import asyncio
import hashlib
import json
import os
import plistlib
import stat
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v2_desktop_backend import Owner, factory
from test_v2_desktop_contracts import response

from omni.adapters.platform.desktop import backend as host
from omni.adapters.platform.desktop import macos_helper as helper
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_request, validate_capture, validate_observation
from omni.domain.desktop_actions import node_proof


def target():
    return {
        "platform": "darwin",
        "backend": "ax",
        "native_id": "42",
        "pid": 123,
        "process_created": 123456.75,
        "application_id": "org.omni.fixture",
        "session_id": "macos:audit:12:uid:501",
    }


def proof():
    return {"backend": "ax", "path": [0, 1], "ancestry_sha256": "a" * 64, "role": "AXButton", "name_sha256": "b" * 64}


def request():
    return {
        "version": VERSION,
        "request_id": str(uuid.uuid4()),
        "operation": "act",
        "limit": 1,
        "target": target(),
        "action": {"kind": "invoke", "text": "", "policy": "desktop-semantic-1", "node": proof()},
    }


@pytest.mark.parametrize(
    "field,value",
    [("path", [0] * 8), ("path", [128]), ("path", [True]), ("ancestry_sha256", "x" * 64), ("extra", True)],
)
def test_ax_reference_is_bounded_and_not_a_native_pointer(field, value):
    value = {**proof(), field: value}
    with pytest.raises(BusinessError):
        node_proof(value)


def test_ax_proof_and_capture_method_cannot_cross_platform_boundaries():
    assert node_proof(proof()) == proof()
    assert parse_request(request())["action"]["node"] == proof()
    wrong = request()
    wrong["target"].update(platform="win32", backend="win32")
    with pytest.raises(BusinessError):
        parse_request(wrong)
    receipt = {
        "target": target(),
        "width": 20,
        "height": 10,
        "bytes": 100,
        "sha256": "c" * 64,
        "method": "screen_capture_kit",
    }
    assert validate_capture(receipt, target()) == receipt
    with pytest.raises(BusinessError):
        validate_capture({**receipt, "method": "print_window"}, target())
    observation = {
        "target": target(),
        "truncated": False,
        "nodes": [
            {
                "id": 1,
                "parent": None,
                "role": "AXButton",
                "name": "",
                "text": "",
                "password": False,
                "enabled": True,
                "offscreen": False,
                "bounds": [0, 0, 20, 10],
                "reference": proof(),
                "actions": ["invoke"],
            }
        ],
    }
    assert validate_observation(observation, target()) == observation
    observation["nodes"][0]["reference"] = {"root": [1], "runtime": [1], "role": "AXButton", "name_sha256": "b" * 64}
    with pytest.raises(BusinessError):
        validate_observation(observation, target())


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / helper.BUNDLE_NAME
    executable = root / "Contents/MacOS" / helper.EXECUTABLE
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"isolated-owned-native-build")
    executable.chmod(0o700)
    info = {
        "CFBundleIdentifier": helper.BUNDLE_ID,
        "CFBundleExecutable": helper.EXECUTABLE,
        "OmniDesktopProtocol": VERSION,
        "LSMinimumSystemVersion": "14.0",
    }
    (root / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    manifest = {
        "format": "omni-macos-helper-build-1",
        "protocol": VERSION,
        "bundle_id": helper.BUNDLE_ID,
        "source_files": helper.source_fingerprints(),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    (tmp_path / (helper.BUNDLE_NAME + ".build.json")).write_text(json.dumps(manifest))
    monkeypatch.setattr(helper, "bundle_path", lambda: root)
    # Permission metadata is injected, allowing Windows CI to exercise the macOS resolver.
    # Actual bytes, descriptors, digests, limits and path traversal remain real.
    original_lstat, original_stat = Path.lstat, Path.stat
    uid = getattr(os, "getuid", lambda: 0)()
    monkeypatch.setattr(helper.os, "getuid", lambda: uid, raising=False)

    def metadata(original):
        def read(path, *args, **kwargs):
            current = original(path, *args, **kwargs)
            values = {name: getattr(current, name) for name in dir(current) if name.startswith("st_")}
            values["st_mode"] = (current.st_mode & ~0o022) | stat.S_IXUSR
            values["st_uid"] = uid
            return SimpleNamespace(**values)

        return read

    monkeypatch.setattr(Path, "lstat", metadata(original_lstat))
    monkeypatch.setattr(Path, "stat", metadata(original_stat))
    return root, executable


def test_resolver_checks_fixed_bundle_protocol_and_final_signed_executable_digest(installed):
    root, executable = installed
    assert helper.resolve() == executable
    executable.write_bytes(b"changed-executable")
    with pytest.raises(BusinessError, match="Rebuild"):
        helper.resolve()
    executable.write_bytes(b"isolated-owned-native-build")
    path = root / "Contents/Info.plist"
    info = plistlib.loads(path.read_bytes())
    info["OmniDesktopProtocol"] = "stale-protocol"
    path.write_bytes(plistlib.dumps(info))
    with pytest.raises(BusinessError):
        helper.resolve()


@pytest.mark.parametrize("fault", ["link", "other_owner", "writable"])
def test_resolver_rejects_unsafe_installation_metadata(installed, monkeypatch, fault):
    root, _ = installed
    prior = Path.lstat

    def bad(path, *args, **kwargs):
        info = prior(path, *args, **kwargs)
        if path == root:
            if fault == "link":
                info.st_mode = stat.S_IFLNK | 0o700
            elif fault == "other_owner":
                info.st_uid += 1
            else:
                info.st_mode |= 0o020
        return info

    monkeypatch.setattr(Path, "lstat", bad)
    with pytest.raises(BusinessError):
        helper.resolve()


def test_missing_bundle_does_not_search_path_or_current_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "bundle_path", lambda: tmp_path / helper.BUNDLE_NAME)
    monkeypatch.setenv("PATH", str(tmp_path))
    (tmp_path / helper.EXECUTABLE).write_bytes(b"untrusted")
    assert helper.resolve() is None


def test_installed_helper_from_different_sources_is_rejected(installed, monkeypatch):
    monkeypatch.setattr(helper, "source_fingerprints", lambda: {"changed.swift": "0" * 64})
    with pytest.raises(BusinessError):
        helper.resolve()


def test_macos_host_selects_fixed_helper_and_reuses_bounded_ownership(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    path = Path("/fixed/owned/helper")
    monkeypatch.setattr(helper, "resolve", lambda: path)

    async def scenario():
        owner = Owner(
            builder=lambda req: (
                json.dumps(response(req, {"matches": True, "target": req["target"], "reason": None})).encode() + b"\n"
            )
        )
        calls = []
        backend = host.DesktopBackend(process_factory=factory(owner, calls=calls))
        try:
            assert (await backend.verify(target()))["matches"]
            assert calls[0][0] == [str(path), "--ipc"]
            assert owner.reclaimed and backend.health()["resident"] == 0
        finally:
            assert await backend.close()

    asyncio.run(scenario())


def test_missing_macos_helper_never_falls_back_to_another_native_executable(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    monkeypatch.setattr(helper, "resolve", lambda: None)

    async def scenario():
        backend = host.DesktopBackend(process_factory=lambda *_a, **_k: pytest.fail("must not spawn"))
        try:
            with pytest.raises(BusinessError) as error:
                await backend.observe(target())
            assert error.value.code == "desktop_macos_helper_missing"

            async def guard():
                pass

            from omni.domain.foreground import POLICY

            command = {
                "kind": "type_text",
                "text": "owned",
                "node": proof(),
                "policy": POLICY,
                "takeover": True,
                **{key: str(uuid.uuid4()) for key in ("grant_id", "run_id", "operation_id")},
            }
            denied = await backend.foreground(target(), command, before_dispatch=guard)
            assert denied["status"] == "denied" and denied["effect"] == "none"
            assert denied["error_code"] == "desktop_macos_helper_missing"
        finally:
            assert await backend.close()

    asyncio.run(scenario())
