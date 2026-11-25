"""Real isolated file permissions are inspected without repairing an unsafe file."""

from __future__ import annotations

from omni_test_support.v2.private_permissions import (
    _security_bytes as _security_bytes,
)
from omni_test_support.v2.private_permissions import (
    _windows_acl as _windows_acl,
)
from omni_test_support.v2.private_permissions import (
    assert_private as assert_private,
)
from omni_test_support.v2.private_permissions import (
    private_path as private_path,
)
from omni_test_support.v2.private_permissions import (
    pytest as pytest,
)
from omni_test_support.v2.private_permissions import (
    sys as sys,
)

from omni.adapters.platform.runtime import permissions


def test_private_file_inspection_does_not_change_permissions_or_contents(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_bytes(b"private identity")
    private_path(path)
    before = _security_bytes(path)
    assert_private(path)
    assert before == _security_bytes(path)
    assert path.read_bytes() == b"private identity"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SID and ownership policy")
def test_windows_private_write_uses_only_the_tokens_default_owner(tmp_path, monkeypatch):
    import ctypes

    path = tmp_path / "candidate.json"
    path.write_bytes(b"private fixture")
    private_path(path)
    before = _security_bytes(path)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    (owner,) = permissions._windows_token_sids(security, kernel, 1)
    system = bytes.fromhex("010100000000000512000000")
    administrators = bytes.fromhex("01020000000000052000000020020000")

    with monkeypatch.context() as patch:
        patch.setattr(permissions, "_windows_token_sids", lambda *_: (system, owner))
        assert permissions._windows_owner_override(path, security, kernel) == "S-1-5-18"
        patch.setattr(permissions, "_windows_token_sids", lambda *_: (system, administrators))
        with pytest.raises(OSError, match="another account"):
            permissions._windows_owner_override(path, security, kernel)

    assert _security_bytes(path) == before


@pytest.mark.parametrize("problem", ["public", "inherited"])
def test_unsafe_permissions_are_rejected_without_repair(problem, tmp_path):
    path = tmp_path / "owner.token"
    path.write_bytes(b"isolated fixture only")
    private_path(path)
    try:
        if sys.platform == "win32":
            _windows_acl(path, public=problem == "public", protected=problem != "inherited")
        else:
            path.chmod(0o644 if problem == "public" else 0o640)
        before = _security_bytes(path)
        with pytest.raises(OSError):
            assert_private(path)
        assert _security_bytes(path) == before
        assert path.read_bytes() == b"isolated fixture only"
    finally:
        private_path(path)
