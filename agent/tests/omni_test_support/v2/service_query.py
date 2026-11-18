"""Reusable service_query fixtures, extracted without collecting test cases."""

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from omni.adapters.platform.services import service_query as q
from omni.adapters.platform.services.service_templates import render
from omni.domain.service_deployment import make_plan, manager_identity


def plan(target):
    windows = target == "windows-user"
    prefix = "C:/fixture" if windows else "/fixture"
    principal = {"name": "owner", "id": "S-1-5-21-1000" if windows else "1000", "home": prefix + "/owner"}
    request = {
        "format": "omni-service-request-1",
        "target": target,
        "instance_id": str(uuid.uuid4()),
        "home": prefix + "/data",
        "working_directory": prefix + "/work",
        "bundle_dir": prefix + "/bundle",
        "principal": principal,
        "host": "127.0.0.1",
        "port": 8765,
        "external_url": None,
        "master_key_file": None,
        "stdout_logs": True,
    }
    runtime = {
        "format": "omni-service-runtime-2",
        "platform": "win32" if windows else "darwin" if target.startswith("macos") else "linux",
        "python": prefix + "/python.exe" if windows else prefix + "/python",
        "python_realpath": prefix + "/python.exe" if windows else prefix + "/python",
        "python_prefix": prefix + "/python-home",
        "python_links": [],
        "venv_config": None,
        "python_sha256": "a" * 64,
        "process_python": prefix + "/python.exe" if windows else prefix + "/python",
        "process_python_sha256": "a" * 64,
        "python_version": "3.12.0",
        "architecture": "x64",
        "package_root": prefix + "/package",
        "build_id": "b" * 64,
        "assets_sha256": "c" * 64,
        "api_version": 1,
        "schema_head": "v2_0026",
        "editable": False,
        "principal": principal,
        "files": [{"path": "composition/service_runner.py", "sha256": "d" * 64, "bytes": 1}],
        "distributions": [],
    }
    return make_plan(request, runtime, str(uuid.uuid4()), "2026-11-05T00:00:00+00:00")


def native(monkeypatch, target):
    monkeypatch.setattr(
        q.sys, "platform", "win32" if target == "windows-user" else "darwin" if target.startswith("macos-") else "linux"
    )
    monkeypatch.setattr(q.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(q.ServiceQuery, "_program", staticmethod(lambda _: "/fixed/manager"))


def systemd(data, **changes):
    identity = manager_identity(data)
    fields = {
        "Id": identity["name"],
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "MainPID": "42",
        "FragmentPath": identity["installed_path"],
        "SourcePath": "",
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "Transient": "no",
    }
    return "".join(f"{name}={value}\n" for name, value in {**fields, **changes}.items()).encode()


class Process:
    def __init__(self, payload=b"", *, eof=True):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(payload)
        if eof:
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        self.returncode = 0 if eof else None

    async def wait(self):
        return self.returncode or 0


class Owner:
    def __init__(self, payload=b"", *, eof=True, outcomes=()):
        self.process = Process(payload, eof=eof)
        self.outcomes, self.reclaimed = list(outcomes), False

    async def terminate(self, _timeout):
        if self.outcomes and not self.outcomes.pop(0):
            return False
        self.reclaimed = True
        self.process.returncode = 0
        if not self.process.stdout.at_eof():
            self.process.stdout.feed_eof()
        if not self.process.stderr.at_eof():
            self.process.stderr.feed_eof()
        return True


def spawn(monkeypatch, owner, *, gate=None, started=None):
    calls = []

    async def owned(_cls, argv, **kwargs):
        calls.append((argv, kwargs))
        if started:
            started.set()
        if gate:
            await gate.wait()
        return owner

    monkeypatch.setattr(q.OwnedProcess, "spawn", classmethod(owned))
    return calls


__all__ = [
    "ET",
    "Owner",
    "Path",
    "Process",
    "asyncio",
    "json",
    "make_plan",
    "manager_identity",
    "native",
    "os",
    "plan",
    "pytest",
    "q",
    "render",
    "spawn",
    "systemd",
    "threading",
    "uuid",
]
