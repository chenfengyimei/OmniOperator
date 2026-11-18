"""Reusable deployment_files fixtures, extracted without collecting test cases."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

import pytest

from omni.adapters.platform.services import deployment_files as d
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import make_plan, validate_runtime
from omni_test_support.v2.private_permissions import _windows_acl


@pytest.fixture
def installation(tmp_path, monkeypatch):
    package = tmp_path / "program" / "omni"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__='isolated'\n", encoding="utf-8")
    for layer in d.LAYERS:
        folder = package / layer
        folder.mkdir()
        (folder / "isolated.py").write_text("VALUE='isolated'\n", encoding="utf-8")
    assets = package / "web_assets"
    (assets / "ui/v2").mkdir(parents=True)
    (assets / "ui/v2/index.html").write_text("<html>isolated</html>", encoding="utf-8")
    prefix = tmp_path / "environment"
    prefix.mkdir()
    (prefix / "bin").mkdir()
    binary = prefix / "bin" / "python.exe"
    binary.write_bytes(b"isolated interpreter identity; never executed")
    config = prefix / "pyvenv.cfg"
    config.write_text("home = isolated-environment\n", encoding="utf-8")
    principal = {
        "name": "owner",
        "id": "S-1-5-21-1000" if sys.platform == "win32" else "1000",
        "home": str(tmp_path / "owner"),
    }
    monkeypatch.setattr(d, "_installation", lambda: (package, assets, False))
    monkeypatch.setattr(d, "_principal", lambda: dict(principal))
    monkeypatch.setattr(d.importlib.metadata, "distributions", lambda: [])

    def interpreter():
        content = d._read(binary, d.MAX_BYTES)
        cfg = d._read(config, d.MAX_BYTES)
        return {
            "python": str(binary),
            "python_realpath": str(binary),
            "python_prefix": str(prefix),
            "python_links": [],
            "python_sha256": hashlib.sha256(content).hexdigest(),
            "process_python": str(binary),
            "process_python_sha256": hashlib.sha256(content).hexdigest(),
            "python_version": sys.version.replace("\r\n", " ").replace("\n", " "),
            "venv_config": {"path": str(config), "sha256": hashlib.sha256(cfg).hexdigest(), "bytes": len(cfg)},
        }

    monkeypatch.setattr(d, "_python", interpreter)
    return d.CurrentDeploymentFiles(), package, assets, binary, config


def plan_for(files, tmp_path, *, runtime=None, bundle="bundle"):
    runtime = files.capture() if runtime is None else runtime
    target = {"win32": "windows-user", "darwin": "macos-user", "linux": "linux-user"}[runtime["platform"]]
    request = {
        "format": "omni-service-request-1",
        "target": target,
        "instance_id": str(__import__("uuid").uuid4()),
        "home": str(tmp_path / "instance"),
        "working_directory": str(tmp_path / "workspace"),
        "bundle_dir": str(tmp_path / bundle),
        "principal": runtime["principal"],
        "host": "127.0.0.1",
        "port": 8766,
        "external_url": None,
        "master_key_file": None,
        "stdout_logs": True,
    }
    return make_plan(
        request,
        runtime,
        str(__import__("uuid").uuid4()),
        dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
    )


__all__ = [
    "BusinessError",
    "Path",
    "_windows_acl",
    "copy",
    "d",
    "dt",
    "hashlib",
    "installation",
    "json",
    "make_plan",
    "os",
    "plan_for",
    "platform",
    "pytest",
    "sys",
    "validate_runtime",
]
