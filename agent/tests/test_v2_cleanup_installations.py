"""Selected old installation proofs use real isolated artifacts; never import old code or execute an interpreter."""

from __future__ import annotations

import subprocess

import pytest
from test_v2_releases import installation as installation
from test_v2_releases import release as release

from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("side", ["source", "target"])
def test_both_quarantined_installations_are_statically_verified_and_changed_code_refused(release, monkeypatch, side):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No old program may execute"))
    r.files.reviewed_installation(r.plan[side])
    package = r.source_package if side == "source" else r.current["package"]
    changed = package / "adapters/isolated.py"
    changed.write_bytes(b"raise RuntimeError('old program must never execute')\n")
    with pytest.raises(BusinessError) as caught:
        r.files.reviewed_installation(r.plan[side])
    assert caught.value.code == "release_source_program_changed"
